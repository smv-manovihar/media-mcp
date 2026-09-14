import streamlit as st
import asyncio
import datetime
import html
import os
import json
import ast
import re
import inspect
import traceback
from pathlib import Path
from typing import Any, Tuple, List, Dict, Optional
from PIL import Image, ImageOps
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import client.prompts as prompts
from config.settings import load_config
from utils import chat_store
from utils.deduplication import save_uploaded_file_deduplicated


def _debug_log(msg: str):
    """Agent debug log disabled — no-op (kept for call-site compatibility)."""
    return
    # try:
    #     with open("agent_debug.log", "a", encoding="utf-8") as f:
    #         f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {msg}\n")
    # except Exception:
    #     pass


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Detect provider quota / rate-limit errors (Gemini 429, Groq 429, OpenAI RateLimit, etc)."""
    name = type(exc).__name__.lower()
    text = f"{name}: {exc}"
    markers = (
        "429",
        "rate limit",
        "ratelimit",
        "too many requests",
        "resource_exhausted",
        "quota",
        "generate_content_free_tier_requests",
        "generateRequestsPerDay".lower(),
    )
    text_low = text.lower()
    return any(m in text_low or m in name for m in markers)


def _format_llm_error(exc: BaseException) -> str:
    """Simple user-friendly message for LLM failures."""
    if _is_rate_limit_error(exc):
        return "Rate limit exceeded. Please wait a minute or try a different model."
    return f"Model request failed ({type(exc).__name__}): {exc}"


def _run_coro_in_fresh_loop(coro):
    """
    Run an async coroutine without reusing the shared init_agent loop.

    The shared loop causes `RuntimeError: This event loop is already running`
    when a new message arrives while a previous stream is still active
    (see agent_debug.log 20:36:21 -> 20:36:56). A fresh loop per request
    is loop-agnostic for LangChain/LangGraph and avoids the race.
    """
    fresh = asyncio.new_event_loop()
    prev = None
    try:
        try:
            prev = asyncio.get_event_loop()
        except Exception:
            prev = None
        asyncio.set_event_loop(fresh)
        return fresh.run_until_complete(coro)
    finally:
        try:
            fresh.close()
        except Exception:
            pass
        try:
            if prev is not None:
                asyncio.set_event_loop(prev)
        except Exception:
            pass


def _extract_token_usage(obj: Any) -> Optional[Dict[str, int]]:
    """
    Safely extract input, output, cached, and total tokens from LangChain
    AIMessage, AIMessageChunk, ChatResult, or response dictionaries.
    """
    if obj is None:
        return None

    usage_meta = getattr(obj, "usage_metadata", None)
    resp_meta = getattr(obj, "response_metadata", None) or {}

    # Handle ChatResult wrappers if present
    if not usage_meta and hasattr(obj, "generations"):
        try:
            gen = obj.generations[0][0]
            usage_meta = getattr(gen.message, "usage_metadata", None)
            if not resp_meta:
                resp_meta = getattr(gen.message, "response_metadata", None) or {}
        except Exception:
            pass

    if not usage_meta and hasattr(obj, "llm_output") and obj.llm_output:
        token_usage = obj.llm_output.get("token_usage") or {}
        if token_usage:
            resp_meta = {"token_usage": token_usage}

    if isinstance(obj, dict):
        usage_meta = obj.get("usage_metadata") or usage_meta
        resp_meta = obj.get("response_metadata") or obj.get("usage") or resp_meta

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cached_tokens = 0

    if isinstance(usage_meta, dict):
        input_tokens = usage_meta.get("input_tokens") or 0
        output_tokens = usage_meta.get("output_tokens") or 0
        total_tokens = usage_meta.get("total_tokens") or 0
        input_details = usage_meta.get("input_token_details") or {}
        if isinstance(input_details, dict):
            cached_tokens = (
                input_details.get("cache_read")
                or input_details.get("cached_tokens")
                or 0
            )

    # Fallback to response_metadata across different providers
    if not (input_tokens or output_tokens):
        usage_dict = (
            resp_meta.get("token_usage")
            or resp_meta.get("usage")
            or resp_meta.get("usage_metadata")
            or resp_meta
        )
        if isinstance(usage_dict, dict):
            input_tokens = (
                usage_dict.get("prompt_tokens")
                or usage_dict.get("prompt_token_count")
                or usage_dict.get("input_tokens")
                or 0
            )
            output_tokens = (
                usage_dict.get("completion_tokens")
                or usage_dict.get("candidates_token_count")
                or usage_dict.get("output_tokens")
                or 0
            )
            total_tokens = (
                usage_dict.get("total_tokens")
                or usage_dict.get("total_token_count")
                or (input_tokens + output_tokens)
            )
            prompt_details = (
                usage_dict.get("prompt_tokens_details")
                or usage_dict.get("input_token_details")
                or {}
            )
            if isinstance(prompt_details, dict):
                cached_tokens = (
                    prompt_details.get("cached_tokens")
                    or prompt_details.get("cache_read")
                    or 0
                )
            if not cached_tokens:
                cached_tokens = (
                    usage_dict.get("cached_content_token_count")
                    or usage_dict.get("cache_read_input_tokens")
                    or 0
                )

    if not total_tokens and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens

    if input_tokens or output_tokens or cached_tokens or total_tokens:
        return {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cached_tokens": int(cached_tokens),
            "total_tokens": int(total_tokens),
        }
    return None


def _render_message_info_button(message: Dict[str, Any]):
    """
    Renders LLM usage info as a single always-visible caption line.

    No button, no tooltip, no expander — everything is inline so there is
    nothing hidden behind hover/click.
    """
    provider = (message.get("provider") or "").strip()
    model = (message.get("model") or "").strip()
    usage = message.get("token_usage")

    # Skip if there is nothing useful to show
    if not provider and not model and not usage:
        return

    def _to_int(v: Any) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    in_tokens = 0
    out_tokens = 0
    cached_tokens = 0
    total_tokens = 0
    has_usage = False

    if isinstance(usage, dict):
        in_tokens = _to_int(usage.get("input_tokens"))
        out_tokens = _to_int(usage.get("output_tokens"))
        cached_tokens = _to_int(usage.get("cached_tokens"))
        total_tokens = _to_int(usage.get("total_tokens")) or (in_tokens + out_tokens)
        has_usage = in_tokens > 0 or out_tokens > 0 or total_tokens > 0

    def _fmt(n: int) -> str:
        return f"{n:,}"

    short_model = model.split("/")[-1].strip() if model else ""
    provider_label = provider.strip() if provider else ""
    if short_model and provider_label:
        model_part = f"{provider_label} · {short_model}"
    else:
        model_part = short_model or provider_label

    if has_usage and total_tokens > 0:
        parts = [f"{_fmt(in_tokens)} in", f"{_fmt(out_tokens)} out"]
        if cached_tokens:
            parts.append(f"{_fmt(cached_tokens)} cached")
        parts.append(f"{_fmt(total_tokens)} total")
        line = f"{model_part} · {' · '.join(parts)}" if model_part else f"{' · '.join(parts)}"
    elif model_part:
        line = f"{model_part} · _no usage reported_"
    else:
        return

    st.caption(line)


def _parse_tool_output(output: Any) -> Tuple[Any, bool]:
    """
    Safely extracts clean, human-readable data from raw LangChain / MCP tool output.
    Returns: (clean_data, is_error)
    """
    if output is None:
        return "", False

    if hasattr(output, "content"):
        output = output.content

    if isinstance(output, str):
        trimmed = output.strip()
        if not trimmed:
            return "", False

        if (
            (trimmed.startswith("[{") and trimmed.endswith("}]"))
            or (trimmed.startswith("{'") and trimmed.endswith("'}"))
            or (trimmed.startswith("['") and trimmed.endswith("']"))
        ):
            try:
                parsed_literal = ast.literal_eval(trimmed)
                return _parse_tool_output(parsed_literal)
            except Exception:
                pass

        if (trimmed.startswith("{") and trimmed.endswith("}")) or (
            trimmed.startswith("[") and trimmed.endswith("]")
        ):
            try:
                parsed_json = json.loads(trimmed)
                return _parse_tool_output(parsed_json)
            except Exception:
                pass

        is_err = bool(re.match(r"^(error|failed|exception|traceback)\b", trimmed, re.IGNORECASE))
        return trimmed, is_err

    if isinstance(output, list):
        if not output:
            return "", False

        texts = []
        is_block_list = False
        for item in output:
            if isinstance(item, dict) and "text" in item and ("type" in item or "id" in item):
                is_block_list = True
                texts.append(item["text"])
            elif isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                texts.append(item)

        if is_block_list:
            combined = "\n".join(t if isinstance(t, str) else json.dumps(t) for t in texts)
            return _parse_tool_output(combined)

        return output, False

    if isinstance(output, dict):
        is_error = False
        if "error" in output or "is_error" in output:
            is_error = True
        elif output.get("status") in ("error", "failed"):
            is_error = True
        elif output.get("success") is False:
            is_error = True
        return output, is_error

    return output, False


def _render_tool_call_expander(tool_call: dict, expanded: bool = False):
    """
    Renders a single tool call with its input and output inside a single collapsible expander.
    """
    name = tool_call.get("name", "Tool")
    args = tool_call.get("args", {})
    output = tool_call.get("output")
    is_error = tool_call.get("is_error", False)
    status = tool_call.get("status", "success")

    if status == "running":
        icon = "⏳"
        label = f"{icon} Tool: `{name}` (running...)"
    elif is_error:
        icon = "❌"
        label = f"{icon} Tool: `{name}` (error)"
    else:
        icon = "🛠️"
        label = f"{icon} Tool: `{name}`"

    with st.expander(label, expanded=expanded):
        with st.expander("📥 Arguments", expanded=True):
            if args and isinstance(args, dict) and any(v is not None for v in args.values()):
                st.json(args)
            elif args:
                st.code(str(args), language="json")
            else:
                st.caption("*(No arguments)*")

        # Output is always collapsed — user must expand to inspect.
        with st.expander("📤 Output", expanded=False):
            if status == "running" and output is None:
                st.caption("⏳ *Executing tool call...*")
            elif is_error:
                if isinstance(output, dict) and "error" in output:
                    st.error(f"❌ **Error:** {output['error']}")
                    extra_keys = {k: v for k, v in output.items() if k != "error"}
                    if extra_keys:
                        st.json(extra_keys)
                elif isinstance(output, dict):
                    st.error(f"❌ **Failed:**\n```json\n{json.dumps(output, indent=2)}\n```")
                elif isinstance(output, str):
                    st.error(f"❌ **Error:** {output}")
                else:
                    st.error(f"❌ **Error:** {str(output)}")
            elif output is not None and output != "":
                if isinstance(output, (dict, list)):
                    st.json(output)
                elif isinstance(output, str):
                    st.markdown(output)
                else:
                    st.write(output)
            else:
                st.caption("*(No output returned)*")


def _display_image_with_exif_fix(image_path: str, width: Any = "stretch"):
    """
    Load and display image with correct orientation preserved.
    Gracefully catches errors if the image cannot be loaded.
    `width` caps the rendered size (int pixels or "stretch"/"content").
    """
    try:
        p = Path(image_path)
        if not p.exists():
            st.caption(f"📷 *Image unavailable: `{p.name}` (not found on disk)*")
            return

        img = Image.open(p)
        if img.mode == "P" and "transparency" in img.info:
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        img = ImageOps.exif_transpose(img)
        st.image(img, width=width, caption=p.name)
    except Exception as e:
        st.caption(f"⚠️ *Unable to preview `{Path(image_path).name}`: {str(e)}*")


def _render_image_grid(image_paths: List[str], num_cols: int = 3, thumb_width: Any = "stretch"):
    """Render images in a responsive grid layout."""
    for i in range(0, len(image_paths), num_cols):
        cols = st.columns(num_cols)
        for j in range(num_cols):
            if i + j < len(image_paths):
                with cols[j]:
                    _display_image_with_exif_fix(image_paths[i + j], width=thumb_width)


def _render_video_player(video_path: str, constrain_width: bool = False):
    """Render video player with playback controls and graceful fallback.

    `constrain_width` renders inside a narrower column so the player does
    not dominate the chat width. `st.video` itself has no width param, so
    the column is what limits its size.
    """
    try:
        p = Path(video_path)
        if not p.exists():
            st.caption(f"🎬 *Video unavailable: `{p.name}` (not found on disk)*")
            return
        if constrain_width:
            col_vid, _spacer = st.columns([3, 2])
            with col_vid:
                st.video(str(p))
                st.caption(f"🎬 {p.name}")
        else:
            st.video(str(p))
            st.caption(f"🎬 {p.name}")
    except Exception as e:
        st.caption(f"⚠️ *Unable to play `{Path(video_path).name}`: {str(e)}*")


def _ensure_file_link_css():
    """Style inline file anchors link-like but differentiable."""
    if st.session_state.get("_file_link_css_done"):
        return
    st.session_state["_file_link_css_done"] = True
    st.markdown(
        "<style>a.mediamcp-file-link{color:#4da3ff!important;text-decoration:underline!important;"
        "text-underline-offset:3px;}a.mediamcp-file-link:hover{color:#82c0ff!important;}</style>",
        unsafe_allow_html=True,
    )


_LINK_ATTR_RE = re.compile(
    r"<(open|reveal)\s+(?:path|src)=[\"']([^\"']+)[\"'][^>]*>(?:[^<]*</(?:open|reveal)\s*>)?",
    re.IGNORECASE,
)
_LINK_INNER_RE = re.compile(
    r"<(open|reveal)\s*>([^<]+)</(?:open|reveal)\s*>",
    re.IGNORECASE,
)


def _linkify_content(content: str) -> str:
    """Replace <open>/<reveal> tags with truly inline links.

    Streamlit buttons are block widgets and can never sit inside a text
    line (e.g. a dot point), so links are raw `<a target="_self">` anchors
    pointing at the local link server, which opens the file and answers
    204 — the page never refreshes.
    """
    from urllib.parse import quote

    try:
        from utils.file_link_server import ensure_server
        port, token = ensure_server()
    except Exception:
        return content or ""

    def _md_link(action: str, raw: str) -> str:
        target = _resolve_link_target(raw)
        name = html.escape(Path(target).name or target)
        icon = "📁" if Path(target).is_dir() else "📄"
        title = html.escape(target, quote=True)
        href = (
            f"http://127.0.0.1:{port}/open?action={action}"
            f"&path={quote(target, safe='')}&t={quote(token, safe='')}"
        )
        return f'<a target="_self" class="mediamcp-file-link" title="{title}" href="{href}">{icon} {name} ↗</a>'

    def _sub(m: re.Match) -> str:
        return _md_link(m.group(1).lower(), m.group(2).strip().strip('"\''))

    content = _LINK_ATTR_RE.sub(_sub, content or "")
    return _LINK_INNER_RE.sub(_sub, content)


def sync_chat_session_url():
    """Adopt `?s=` chat session from the URL; keep URL reflecting active chat.

    Called once per script run from app.py before rendering history.
    Refresh-safe and shareable; never reruns by itself.
    """
    from utils.session_url import sync_session_url, read_session_param
    sid = read_session_param()
    if sid:
        try:
            if sid != st.session_state.get("active_session_id") and chat_store.get_session(sid):
                st.session_state.active_session_id = sid
                st.session_state.messages = chat_store.get_session_messages(sid)
        except Exception:
            pass
    try:
        sync_session_url(st.session_state.get("active_session_id"))
    except Exception:
        pass


def _resolve_link_target(raw: str) -> str:
    """Resolve a link-tag path to an absolute string (kept even if missing)."""
    try:
        p = Path(str(raw).strip().strip('"\'')).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        return str(p.resolve())
    except Exception:
        return str(raw).strip()


def _render_content_inline(content: str, key_prefix: str = "") -> str:
    """
    Renders message content with <render>, <img>, and <video> tags displayed inline
    at the position they appear in the text.
    Text segments are rendered as st.markdown(), media rendered at tag position.
    Consecutive images are grouped into a grid (up to 3 per row, fixed size).
    Returns cleaned text (tags removed) for persistence.
    Must be called inside an appropriate st container context.
    """
    render_attr_re = re.compile(
        r'<render\s+(?:path|src)=["\']([^"\']+)["\']\s*(?:/>|>(?:\s*</render>)?|>)',
        re.IGNORECASE,
    )
    render_inner_re = re.compile(r'<render>([^<]+)</render>', re.IGNORECASE)
    img_re = re.compile(r'<img\s+(?:path|src)=["\']([^"\']+)["\']\s*(?:/>|></img>|>)', re.IGNORECASE)
    video_re = re.compile(r'<video\s+(?:path|src)=["\']([^"\']+)["\']\s*(?:/>|></img>|>|</video>)', re.IGNORECASE)

    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv"}

    def _determine_media_type(p_str: str) -> str:
        clean = p_str.strip().strip('"\'')
        return "video" if Path(clean).suffix.lower() in video_exts else "image"

    # Link tags become inline anchors first, so they flow inside
    # dot points; media tags are then rendered at their positions below.
    _ensure_file_link_css()
    content = _linkify_content(content or "")

    # Collect all media tag positions sorted by occurrence
    media_matches: List[Tuple[str, int, int, str]] = []
    for m in render_attr_re.finditer(content):
        p_val = m.group(1).strip().strip('"\'')
        media_matches.append((_determine_media_type(p_val), m.start(), m.end(), p_val))
    for m in render_inner_re.finditer(content):
        p_val = m.group(1).strip().strip('"\'')
        media_matches.append((_determine_media_type(p_val), m.start(), m.end(), p_val))
    for m in img_re.finditer(content):
        p_val = m.group(1).strip().strip('"\'')
        media_matches.append(("image", m.start(), m.end(), p_val))
    for m in video_re.finditer(content):
        p_val = m.group(1).strip().strip('"\'')
        media_matches.append(("video", m.start(), m.end(), p_val))
    media_matches.sort(key=lambda x: x[1])

    cleaned_parts: List[str] = []

    def _clean_text(raw: str) -> str:
        raw = re.sub(r"##\s+(?:Image|Media|Video)\s+References\s*\n*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"</render>", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"<(?:open|reveal)\s*>([^<]*)</(?:open|reveal)\s*>", r"\1", raw, flags=re.IGNORECASE)
        raw = re.sub(r"<(?:open|reveal)\s+(?:path|src)=[\"'][^\"']+[\"'][^>]*>(?:[^<]*</(?:open|reveal)\s*>)?", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"</(?:open|reveal)\s*>", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        return raw.strip()

    def _render_text_segment(raw: str):
        cleaned = _clean_text(raw)
        if cleaned:
            cleaned_parts.append(cleaned)
            # Raw anchors need unescaped HTML; plain text keeps safe escaping.
            st.markdown(cleaned, unsafe_allow_html="mediamcp-file-link" in cleaned)

    def _resolve_path(path_str: str):
        p_clean = path_str.replace("\\", os.sep).replace("/", os.sep)
        p = Path(p_clean)
        if not p.is_absolute():
            p = Path.cwd() / p
        try:
            full_p = p.resolve()
            return full_p if full_p.exists() else None
        except Exception:
            return None

    def _render_image_group(paths: List[str]):
        resolved = [str(r) for r in (_resolve_path(p) for p in paths) if r is not None]
        missing = len(paths) - len(resolved)
        if missing:
            st.caption(f"ℹ️ *{missing} referenced image(s) not found on disk.*")
        if not resolved:
            return
        _render_image_grid(resolved, num_cols=3)

    def _render_video_item(path_str: str):
        full_p = _resolve_path(path_str)
        if full_p:
            cols = st.columns(2)
            with cols[0]:
                _render_video_player(str(full_p))
        else:
            st.caption(f"ℹ️ *Referenced video not found: `{path_str}`*")

    if not media_matches:
        _render_text_segment(content)
    else:
        last_end = 0
        i = 0
        while i < len(media_matches):
            media_type, start, end, path_str = media_matches[i]

            # Text before this media tag
            if start > last_end:
                _render_text_segment(content[last_end:start])

            if media_type == "image":
                # Batch consecutive image tags (only whitespace between them)
                img_group = [path_str]
                j = i + 1
                while j < len(media_matches):
                    nxt_type, nxt_start, nxt_end, nxt_path = media_matches[j]
                    between = content[end:nxt_start]
                    if nxt_type == "image" and not between.strip():
                        img_group.append(nxt_path)
                        end = nxt_end
                        j += 1
                    else:
                        break
                _render_image_group(img_group)
                last_end = end
                i = j
            else:
                _render_video_item(path_str)
                last_end = end
                i += 1

        if last_end < len(content):
            _render_text_segment(content[last_end:])

    return "\n\n".join(cleaned_parts)


def parse_and_render_images(content: str, is_historical: bool = False) -> str:
    """Legacy wrapper for backward compatibility."""
    return _render_content_inline(content, key_prefix="legacy")


def display_greeting():
    """Display welcome message on first load."""
    with st.chat_message("assistant"):
        st.markdown(
            "👋 Hi! I'm **MediaMCP**, your intelligent local media and file assistant.\n\n"
            "I can help you:\n"
            "- 📂 Create, read, organize, or search files across formats (PDF, Office, Code, Data)\n"
            "- 🖼️ Inspect photos, analyze camera EXIF/GPS, or search semantically\n"
            "- 🎬 Preview images, videos, and media files directly in the chat\n"
            "- 🔎 Search the web with live summarization\n\n"
            "👉 Use the **sidebar** to manage conversations, folders, and model settings. What would you like to do?"
        )


def display_chat_history():
    """Render all messages from the active session chat history."""
    msgs = st.session_state.messages
    for idx, message in enumerate(msgs):
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                # 1. Assistant reasoning / thinking
                if message.get("reasoning"):
                    with st.expander("🧠 Reasoning & Steps", expanded=False):
                        formatted_reasoning = "\n".join(
                            [f"> {line}" for line in message["reasoning"].strip().split("\n")]
                        )
                        st.markdown(formatted_reasoning)

                # 2. Thought process — pre/inter-tool monologue + tool calls
                monologue = (message.get("monologue") or "").strip()
                tcs = message.get("tool_calls") or []
                if monologue or tcs:
                    with st.expander("🧠 Thought process", expanded=False):
                        if monologue:
                            st.markdown(monologue)
                            if tcs:
                                st.markdown("---")
                        for tc in tcs:
                            _render_tool_call_expander(tc, expanded=False)

                # 3. Render text and media inline in order
                _render_content_inline(message["content"], key_prefix=f"hist{idx}")

                # 4. Message info button hover (model, provider, tokens)
                _render_message_info_button(message)
            else:
                st.markdown(message["content"])

                # Display user-uploaded files
                if message.get("file_path"):
                    _display_user_files(message["file_path"])


def _display_user_files(file_path: Any):
    """Display files uploaded by user in chat history.

    Images and videos are collapsed by default so attachments do not
    dominate the chat. Images render as small thumbnails (4 per row,
    capped at ~240px); videos render via `st.video` (local path, with
    playback controls) inside a width-constrained column.
    """
    file_paths = file_path if isinstance(file_path, list) else [file_path]
    if not file_paths or file_paths == ["None"]:
        return

    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv"}

    image_paths = [p for p in file_paths if Path(p).suffix.lower() in image_exts]
    video_paths = [p for p in file_paths if Path(p).suffix.lower() in video_exts]
    other_files = [
        p for p in file_paths
        if p not in image_paths and p not in video_paths
    ]

    if image_paths:
        label = f"🖼️ {len(image_paths)} image{'s' if len(image_paths) != 1 else ''} attached"
        with st.expander(label, expanded=False):
            # NOTE: width="stretch" serves the full-res image and lets the
            # browser scale it to the column — passing an int width (e.g. 240)
            # makes Streamlit downsample server-side, which looks soft and
            # discards detail. Compactness comes from the collapsed expander
            # + 4-per-row grid, not from downsampling.
            _render_image_grid(image_paths, num_cols=4, thumb_width="stretch")
    if video_paths:
        label = f"🎬 {len(video_paths)} video{'s' if len(video_paths) != 1 else ''} attached"
        with st.expander(label, expanded=False):
            for vp in video_paths:
                _render_video_player(vp, constrain_width=True)
    if other_files:
        for fp in other_files:
            _render_file_download_button(fp)


def _render_file_download_button(file_path: str):
    """Render download button for non-media files."""
    filename = os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            data = f.read()

        mime_type = _get_mime_type(filename)
        st.download_button(
            label=f"📄 {filename}",
            data=data,
            file_name=filename,
            mime=mime_type,
            width="content",
        )
    except Exception as e:
        st.caption(f"📄 `{filename}` (local file)")


def _get_mime_type(filename: str) -> str:
    """Determine MIME type based on file extension."""
    ext = Path(filename).suffix.lower()
    mime_types = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
        ".csv": "text/csv",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".zip": "application/zip",
    }
    return mime_types.get(ext, "application/octet-stream")


def handle_user_input(agent: Any, agent_loop: Any):
    """Handle user input with native stop button, file deduplication, and persistence."""
    user_input = st.chat_input(
        "Ask me anything...",
        accept_file=True,
        submit_mode="stop",
    )

    if not user_input:
        return

    files = getattr(user_input, "files", []) or []
    prompt = (getattr(user_input, "text", "") or "").strip()

    # Save uploaded files with deduplication
    saved_paths = []
    duplicate_notices = []
    for uploaded_file in files:
        canonical_path, is_dup, _ = save_uploaded_file_deduplicated(uploaded_file, target_dir="uploads")
        saved_paths.append(canonical_path)
        if is_dup:
            duplicate_notices.append(Path(canonical_path).name)

    if duplicate_notices:
        st.toast(f"ℹ️ Reused existing file(s): {', '.join(duplicate_notices)}")

    if not prompt and not saved_paths:
        return

    # Handle file-only uploads
    if not prompt:
        if saved_paths:
            st.info("📁 Files uploaded (no text entered)")
            _display_user_files(saved_paths)
        return

    file_path_value = (
        saved_paths[0]
        if len(saved_paths) == 1
        else saved_paths if saved_paths else None
    )

    now_ts = datetime.datetime.now().strftime("%B %d, %Y %H:%M:%S")

    # Capture active LLM provider and model for message metadata
    config = load_config()
    cur_provider = getattr(config, "llm_provider", "") or ""
    cur_model = getattr(config, "llm_model", "") or ""

    # Add to in-memory session state
    user_msg = {
        "role": "user",
        "content": prompt,
        "timestamp": now_ts,
        "file_path": file_path_value,
    }
    st.session_state.messages.append(user_msg)

    # Persist user message to SQLite (lazily create session on first message)
    active_session_id = st.session_state.get("active_session_id")
    if not active_session_id or not chat_store.get_session(active_session_id):
        if prompt:
            clean_text = prompt.strip().replace("\n", " ")
            words = clean_text.split()
            initial_title = " ".join(words[:6])
            if len(initial_title) > 40:
                initial_title = initial_title[:37] + "..."
        elif saved_paths:
            initial_title = Path(saved_paths[0]).name
        else:
            initial_title = "New Chat"

        active_session_id = chat_store.create_session(title=initial_title)
        st.session_state.active_session_id = active_session_id
        try:
            from utils.session_url import sync_session_url
            sync_session_url(active_session_id)
        except Exception:
            pass

    chat_store.add_message(
        session_id=active_session_id,
        role="user",
        content=prompt,
        file_path=file_path_value,
        timestamp=now_ts,
        provider=cur_provider,
        model=cur_model,
        token_usage=None,
    )

    # Display user message in UI
    with st.chat_message("user"):
        st.markdown(prompt)
        if saved_paths:
            _display_user_files(saved_paths)

    # Process with agent
    if agent and agent_loop:
        _process_agent_response(agent, agent_loop)
    else:
        st.warning("⚠️ Agent is not initialized. Please check the console for errors.")


def _process_agent_response(agent: Any, agent_loop: Any):
    """
    Process and stream agent response using LangChain event streaming.
    Provides live stage-aware status indicator, chronological inter-tool monologue routing,
    and native stop handling with SQLite persistence.
    """
    _debug_log("=== _process_agent_response CALLED ===")
    _debug_log(
        f"Agent: {type(agent)}, Loop: {agent_loop} "
        f"(is_running={getattr(agent_loop, 'is_running', lambda: None)()}, "
        f"is_closed={getattr(agent_loop, 'is_closed', lambda: None)()})"
    )

    with st.chat_message("assistant"):
        reasoning_placeholder = st.empty()
        tool_calls_placeholder = st.empty()  # Thought process (live open, closed after final)
        status_holder = st.empty()  # spinner status — after Thought process

        def _set_status(label: str):
            """Show status as normal text with spinner on the left (no box)."""
            with status_holder.container():
                st.markdown(
                    """
                    <style>
                    @keyframes mediamcp-spin { to { transform: rotate(360deg); } }
                    .mediamcp-status {
                        display: flex;
                        align-items: center;
                        gap: 8px;
                        color: var(--text-color);
                        opacity: 0.75;
                        font-size: 0.9rem;
                        padding: 0.25rem 0;
                    }
                    .mediamcp-spinner {
                        display: inline-block;
                        width: 14px;
                        height: 14px;
                        border: 2px solid rgba(128, 128, 128, 0.3);
                        border-top-color: currentColor;
                        border-radius: 50%;
                        animation: mediamcp-spin 0.8s linear infinite;
                        flex-shrink: 0;
                    }
                    </style>
                    """
                    '<div class="mediamcp-status"><span class="mediamcp-spinner"></span>'
                    f"<span>{label}</span></div>",
                    unsafe_allow_html=True,
                )

        _set_status("MediaMCP is thinking...")
        # Final answer below status (matches history order).
        message_placeholder = st.empty()

        # Dynamic prompt template
        date_str = f"Current Date and Time: {datetime.datetime.now()}"
        config = load_config()
        custom_instructions = (
            getattr(config, "llm_custom_instructions", None)
            or getattr(config, "llm_system_prompt", "")
        ).strip()
        base_prompt = prompts.build_system_prompt(custom_instructions)
        system_prompt = f"{base_prompt}\n\n{date_str}"
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        state = {
            "reasoning": "",
            # Pre-tool / inter-tool narration (e.g. "Let me look that up...").
            # This is thought-process chatter, NOT the final answer.
            "monologue": "",
            # Buffer for the current model turn; routed on on_chat_model_end.
            "turn_text": "",
            "turn_has_tools": False,
            "tool_calls_data": {},
            "final_answer": "",
            "raw_final_answer": "",
            "has_content": False,
            "stopped": False,
            # Accumulated token usage across all model turns in this response
            "token_usage": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "total_tokens": 0},
        }

        def _append_to_monologue(text: str):
            """Move intermediate narration into the thought-process buffer."""
            cleaned = (text or "").strip()
            if not cleaned:
                return
            if state["monologue"]:
                state["monologue"] += "\n\n" + cleaned
            else:
                state["monologue"] = cleaned

        def _render_live_thought_process(expanded: bool = True):
            """Live Thought process view: monologue + actual tool-call entries.

            Always collapsed — Streamlit keeps the first expanded=True state
            sticky, so auto-opening live leaves the tool stuck open in the
            final / history view. Progress is shown via the status text.
            """
            mono = state["monologue"].strip()
            tcs = list(state["tool_calls_data"].values())
            if not mono and not tcs:
                return
            with tool_calls_placeholder.container():
                with st.expander("🧠 Thought process", expanded=expanded):
                    if mono:
                        st.markdown(mono)
                    if mono and tcs:
                        st.markdown("---")
                    for tc in tcs:
                        # Never auto-open live; user expands manually.
                        # Arguments auto-opens once the tool itself is opened.
                        _render_tool_call_expander(tc, expanded=False)

        def _render_monologue_preview(expanded: bool = True):
            """Legacy wrapper — now renders full live thought process."""
            _render_live_thought_process(expanded=expanded)

        def _flush_turn_as_monologue():
            """Current turn ended with tool calls -> its text was narration."""
            turn_text = (state.get("turn_text") or "").strip()
            if turn_text:
                _append_to_monologue(turn_text)
                _debug_log(f"  turn routed to MONOLOGUE: {repr(turn_text)[:200]}")
            state["turn_text"] = ""
            state["turn_has_tools"] = False
            # Remove the interim narration from the main answer view.
            if not state["final_answer"]:
                message_placeholder.empty()
            else:
                message_placeholder.markdown(state["final_answer"] + "▌")
            _render_monologue_preview(expanded=True)

        def _flush_turn_as_final():
            """Current turn had no tool calls -> its text is (part of) the answer."""
            turn_text = state.get("turn_text") or ""
            if turn_text:
                state["raw_final_answer"] += turn_text
                state["final_answer"] += turn_text
                message_placeholder.markdown(state["final_answer"] + "▌")
            state["turn_text"] = ""
            state["turn_has_tools"] = False

        def _chunk_has_tool_calls(chunk) -> bool:
            """Detect tool-call signals on a streamed model chunk (all providers)."""
            try:
                if getattr(chunk, "tool_calls", None):
                    return True
                if getattr(chunk, "tool_call_chunks", None):
                    return True
                if getattr(chunk, "invalid_tool_calls", None):
                    return True
                ak = getattr(chunk, "additional_kwargs", {}) or {}
                if ak.get("tool_calls") or ak.get("function_call") or ak.get("tool_call_chunks"):
                    return True
            except Exception:
                pass
            return False

        async def stream_agent_response(state_dict):
            # Context compaction: send last 10 messages to avoid token blowouts
            recent_msgs = (
                st.session_state.messages[-10:]
                if len(st.session_state.messages) > 10
                else st.session_state.messages
            )
            _debug_log(f"recent_msgs count: {len(recent_msgs)}")

            message_tuples = []
            for msg in recent_msgs:
                role = msg.get("role", "user")
                if role == "user":
                    content = _format_message_for_agent(msg)
                else:
                    content = msg.get("content") or ""
                if content:
                    message_tuples.append((role, content))

            formatted_messages = prompt_template.format_messages(messages=message_tuples)
            _debug_log(f"formatted_messages count: {len(formatted_messages)}")
            for idx, fm in enumerate(formatted_messages):
                _debug_log(f"  formatted[{idx}]: {type(fm).__name__} content={repr(fm.content)[:100]}")

            # LangChain event streaming with strict arrival-order sequencing
            _debug_log("Calling agent.astream_events(version='v2')...")
            events_gen = agent.astream_events(
                {"messages": formatted_messages},
                version="v2",
            )
            _debug_log(f"events_gen type: {type(events_gen)}")
            if inspect.iscoroutine(events_gen):
                stream = await events_gen
            else:
                stream = events_gen
            _debug_log(f"stream type: {type(stream)}")

            event_count = 0

            async for event in stream:
                event_count += 1
                event_name = event.get("event")
                _debug_log(f"Event #{event_count}: name={event_name}, event_keys={list(event.keys())}")

                if event_name == "on_chat_model_start":
                    _set_status("MediaMCP is thinking...")
                    state_dict["turn_text"] = ""
                    state_dict["turn_has_tools"] = False

                elif event_name == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if not chunk:
                        _debug_log(f"  on_chat_model_stream: empty chunk in event #{event_count}")
                        continue

                    # 1. Process content_blocks (standard LangChain arrival-order format)
                    content_blocks = getattr(chunk, "content_blocks", None) or []
                    _debug_log(
                        f"  chunk: blocks={content_blocks}, "
                        f"content={repr(getattr(chunk, 'content', None))[:80]}, "
                        f"text={repr(getattr(chunk, 'text', None))[:80]}, "
                        f"tc={getattr(chunk, 'tool_calls', None)}"
                    )

                    # Any tool-call signal on this chunk means the current turn
                    # will end with tool calls -> its text is narration, not final.
                    if _chunk_has_tool_calls(chunk):
                        state_dict["turn_has_tools"] = True

                    if content_blocks:
                        for block in content_blocks:
                            b_type = (block.get("type") or "").lower()
                            if b_type in ("reasoning", "thought"):
                                r_piece = block.get("text") or block.get("reasoning") or block.get("thought") or ""
                                if r_piece:
                                    state_dict["reasoning"] += r_piece
                                    _set_status("Thinking...")
                                    with reasoning_placeholder.container():
                                        with st.expander("🧠 Reasoning & Steps", expanded=False):
                                            st.markdown(state_dict["reasoning"])
                            elif "tool" in b_type or "function" in b_type:
                                # tool_call_chunk / function_call_chunk -> narration turn
                                state_dict["turn_has_tools"] = True
                            elif b_type == "text":
                                t_piece = block.get("text", "")
                                if t_piece:
                                    _set_status("MediaMCP is responding...")
                                    # Buffer per-turn; routed on model_end / tool_start.
                                    state_dict["turn_text"] += t_piece
                                    live = state_dict["final_answer"] + state_dict["turn_text"]
                                    message_placeholder.markdown(live + "▌")
                            elif block.get("text"):
                                # Unknown block carrying text -> treat as narration candidate
                                t_piece = block.get("text", "")
                                state_dict["turn_text"] += t_piece
                                live = state_dict["final_answer"] + state_dict["turn_text"]
                                message_placeholder.markdown(live + "▌")
                    else:
                        # Fallback for models or chunks without content_blocks
                        reasoning_piece = ""
                        if hasattr(chunk, "reasoning") and chunk.reasoning:
                            reasoning_piece = "".join(str(r) for r in chunk.reasoning) if isinstance(chunk.reasoning, list) else str(chunk.reasoning)
                        elif "reasoning_content" in getattr(chunk, "additional_kwargs", {}):
                            reasoning_piece = str(chunk.additional_kwargs["reasoning_content"] or "")
                        elif "thought" in getattr(chunk, "additional_kwargs", {}):
                            reasoning_piece = str(chunk.additional_kwargs["thought"] or "")

                        if reasoning_piece:
                            state_dict["reasoning"] += reasoning_piece
                            _set_status("Thinking...")
                            with reasoning_placeholder.container():
                                with st.expander("🧠 Reasoning & Steps", expanded=False):
                                    st.markdown(state_dict["reasoning"])

                        text_piece = ""
                        if hasattr(chunk, "text") and chunk.text:
                            text_piece = "".join(str(t) for t in chunk.text) if isinstance(chunk.text, list) else str(chunk.text)
                        elif isinstance(chunk.content, str):
                            text_piece = chunk.content
                        elif isinstance(chunk.content, list):
                            text_piece = "".join(
                                item.get("text", "") if isinstance(item, dict) else str(item)
                                for item in chunk.content
                            )

                        if text_piece:
                            _set_status("MediaMCP is responding...")
                            state_dict["turn_text"] += text_piece
                            live = state_dict["final_answer"] + state_dict["turn_text"]
                            message_placeholder.markdown(live + "▌")

                elif event_name == "on_chat_model_end":
                    # A turn ending WITH tool calls carried narration, not the answer.
                    output = (event.get("data") or {}).get("output")
                    try:
                        if output is not None:
                            out_tc = getattr(output, "tool_calls", None) or []
                            out_invalid = getattr(output, "invalid_tool_calls", None) or []
                            if out_tc or out_invalid:
                                state_dict["turn_has_tools"] = True
                            elif isinstance(output, dict) and (
                                output.get("tool_calls") or output.get("function_call")
                            ):
                                state_dict["turn_has_tools"] = True
                    except Exception:
                        pass

                    # Accumulate token usage from this model turn
                    try:
                        usage = _extract_token_usage(output)
                        if usage:
                            tu = state_dict["token_usage"]
                            tu["input_tokens"] += usage.get("input_tokens", 0)
                            tu["output_tokens"] += usage.get("output_tokens", 0)
                            tu["cached_tokens"] += usage.get("cached_tokens", 0)
                            tu["total_tokens"] += usage.get("total_tokens", 0)
                            _debug_log(f"  token_usage accumulated: {tu}")
                    except Exception:
                        pass

                    _debug_log(
                        f"  on_chat_model_end: turn_has_tools={state_dict['turn_has_tools']}, "
                        f"turn_text={repr(state_dict.get('turn_text',''))[:200]}"
                    )
                    if state_dict["turn_has_tools"]:
                        _flush_turn_as_monologue()
                    else:
                        _flush_turn_as_final()

                elif event_name == "on_tool_start":
                    # Safety net: if a turn's text hasn't been routed yet (e.g.
                    # missing on_chat_model_end), a starting tool proves it was
                    # narration, not the final answer.
                    if (state_dict.get("turn_text") or "").strip():
                        _flush_turn_as_monologue()
                    elif state_dict.get("turn_has_tools"):
                        state_dict["turn_text"] = ""
                        state_dict["turn_has_tools"] = False
                    tc_id = event.get("run_id") or f"tc_{len(state_dict['tool_calls_data'])}"
                    tc_name = event.get("name", "Tool")
                    tc_args = event.get("data", {}).get("input", {})
                    _debug_log(f"  on_tool_start: name={tc_name}, args={tc_args}, run_id={tc_id}")
                    _set_status(f"Running {tc_name}...")

                    tc_data = {
                        "id": tc_id,
                        "name": tc_name,
                        "args": tc_args,
                        "output": None,
                        "is_error": False,
                        "status": "running",
                    }
                    state_dict["tool_calls_data"][tc_id] = tc_data
                    # Live: keep Thought process open, tool collapsed (status line shows progress).
                    _render_live_thought_process(expanded=True)

                elif event_name == "on_tool_end":
                    tc_id = event.get("run_id")
                    raw_output = event.get("data", {}).get("output")
                    _debug_log(f"  on_tool_end: run_id={tc_id}, raw_output={repr(raw_output)[:200]}")
                    clean_output, is_err = _parse_tool_output(raw_output)

                    tc_data = state_dict["tool_calls_data"].get(tc_id)
                    if not tc_data:
                        for item in state_dict["tool_calls_data"].values():
                            if item["status"] == "running":
                                tc_data = item
                                break

                    if tc_data:
                        tc_data["output"] = clean_output
                        tc_data["is_error"] = is_err
                        tc_data["status"] = "error" if is_err else "success"

                    _set_status("Analyzing results...")
                    # Live: keep Thought process open, tool collapsed.
                    _render_live_thought_process(expanded=True)

            # Stream ended mid-turn (e.g. stop / error): route any leftover text.
            leftover = (state_dict.get("turn_text") or "").strip()
            if leftover:
                if state_dict.get("turn_has_tools") or state_dict["tool_calls_data"]:
                    _flush_turn_as_monologue()
                else:
                    _flush_turn_as_final()

            _debug_log(
                f"stream_agent_response FINISHED loop. Total events: {event_count}, "
                f"raw_final_answer len={len(state_dict['raw_final_answer'])}, "
                f"monologue len={len(state_dict['monologue'])}, "
                f"reasoning len={len(state_dict['reasoning'])}, "
                f"tool_calls count={len(state_dict['tool_calls_data'])}"
            )

        _stream_exception = None
        try:
            # Use a fresh loop per request — never reuse the shared init_agent loop.
            # Reusing it crashes with "This event loop is already running" when a
            # second message arrives while the previous stream is still active.
            _run_coro_in_fresh_loop(stream_agent_response(state))
        except BaseException as e:
            err_name = type(e).__name__
            is_control_signal = any(k in err_name for k in ("Stop", "Rerun", "KeyboardInterrupt", "ScriptControl"))
            _debug_log(f"STREAM EXCEPTION: {err_name}: {e} (is_control_signal={is_control_signal})\n{traceback.format_exc()}")
            if is_control_signal:
                # User stopped the response — mark it and let finally save, then re-raise
                state["stopped"] = True
                state["final_answer"] += "\n\n*(Response stopped by user)*"
                state["raw_final_answer"] += "\n\n*(Response stopped by user)*"
                _stream_exception = e
            elif err_name == "RuntimeError" and "already running" in str(e).lower():
                friendly = "A previous response is still streaming. Please wait or press Stop, then try again."
                state["raw_final_answer"] += f"\n\n{friendly}"
                state["final_answer"] += f"\n\n{friendly}"
            else:
                # LLM / streaming error — saved as normal chat message only (no st.warning to avoid duplicate)
                friendly = _format_llm_error(e)
                state["raw_final_answer"] += f"\n\n{friendly}"
                state["final_answer"] += f"\n\n{friendly}"
        finally:
            status_holder.empty()

            # Collapse reasoning expander when finished
            if state["reasoning"]:
                formatted_reasoning = "\n".join(
                    [f"> {line}" for line in state["reasoning"].strip().split("\n")]
                )
                reasoning_placeholder.empty()
                with reasoning_placeholder.container():
                    with st.expander("🧠 Reasoning & Steps", expanded=False):
                        st.markdown(formatted_reasoning)

            # Prepare serializable tool calls
            serializable_tool_calls = [
                {
                    "id": tc.get("id"),
                    "name": tc.get("name"),
                    "args": tc.get("args"),
                    "output": tc.get("output"),
                    "is_error": tc.get("is_error", False),
                    "status": tc.get("status", "success"),
                }
                for tc in state["tool_calls_data"].values()
            ]

            final_answer = state["raw_final_answer"].strip()
            monologue = state["monologue"].strip()
            _debug_log(
                f"FINALLY: raw_final_answer={repr(state['raw_final_answer'])}, "
                f"monologue={repr(state['monologue'])}, "
                f"reasoning={repr(state['reasoning'])}, "
                f"serializable_tool_calls={len(serializable_tool_calls)}"
            )

            if not final_answer:
                if state["reasoning"].strip():
                    # Reasoning content was the actual response (edge case: model responded
                    # entirely inside a reasoning block without emitting a separate text turn)
                    final_answer = state["reasoning"].strip()
                    state["reasoning"] = ""  # Don't double-render it
                elif serializable_tool_calls:
                    final_answer = "I have executed the requested tools and completed your request."
                else:
                    final_answer = "I processed your request, but no response text was returned."

            _debug_log(f"FINALLY: calculated final_answer={repr(final_answer)}")


            # Thought process: pre/inter-tool narration + tool calls (never final text)
            # Closed after final response — open only live while streaming.
            if monologue or serializable_tool_calls:
                tool_calls_placeholder.empty()
                with tool_calls_placeholder.container():
                    with st.expander("🧠 Thought process", expanded=False):
                        if monologue:
                            st.markdown(monologue)
                            if serializable_tool_calls:
                                st.markdown("---")
                        for tc in serializable_tool_calls:
                            _render_tool_call_expander(tc, expanded=False)

            # Collect final provider/model and token usage for metadata
            cfg_now = load_config()
            asst_provider = getattr(cfg_now, "llm_provider", "") or ""
            asst_model = getattr(cfg_now, "llm_model", "") or ""
            tu = state.get("token_usage") or {}
            asst_token_usage = tu if any(tu.values()) else None

            # Persist to in-memory session state
            assistant_msg = {
                "role": "assistant",
                "content": final_answer,
                "reasoning": state["reasoning"],
                "monologue": monologue,
                "tool_calls": serializable_tool_calls,
                "timestamp": datetime.datetime.now().strftime("%B %d, %Y %H:%M:%S"),
                "provider": asst_provider,
                "model": asst_model,
                "token_usage": asst_token_usage,
            }
            st.session_state.messages.append(assistant_msg)

            # Render info hover button (model, provider, token counts)
            with message_placeholder.container():
                _render_content_inline(final_answer, key_prefix="live")
                _render_message_info_button(assistant_msg)

            # Persist to SQLite
            active_session_id = st.session_state.get("active_session_id")
            if active_session_id:
                chat_store.add_message(
                    session_id=active_session_id,
                    role="assistant",
                    content=final_answer,
                    reasoning=state["reasoning"],
                    tool_calls=serializable_tool_calls,
                    monologue=monologue,
                    provider=asst_provider,
                    model=asst_model,
                    token_usage=asst_token_usage,
                )

            if _stream_exception:
                raise _stream_exception


def _format_message_for_agent(msg: Dict[str, Any]) -> str:
    """Format message for agent consumption with clean file details."""
    if msg.get("role") != "user":
        return msg.get("content", "")

    uploaded = msg.get("file_path")
    if uploaded and uploaded != "None" and uploaded != ["None"]:
        uploaded_txt = str(uploaded)
        return (
            f"Message details:\n"
            f"- Sent at: {msg.get('timestamp', '')}\n"
            f"- Uploaded file: {uploaded_txt}\n"
            f"---\n"
            f"Message content:\n{msg.get('content', '')}"
        )
    return msg.get("content", "")
