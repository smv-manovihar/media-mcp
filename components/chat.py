import streamlit as st
import datetime
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
    """Append debug logs to agent_debug.log to trace exact streaming objects and execution."""
    try:
        with open("agent_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {msg}\n")
    except Exception:
        pass


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
        st.markdown("**📥 Arguments:**")
        if args and isinstance(args, dict) and any(v is not None for v in args.values()):
            st.json(args)
        elif args:
            st.code(str(args), language="json")
        else:
            st.caption("*(No arguments)*")

        st.markdown("---")

        st.markdown("**📤 Output:**")
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


def _display_image_with_exif_fix(image_path: str):
    """
    Load and display image with correct orientation preserved.
    Gracefully catches errors if the image cannot be loaded.
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
        st.image(img, width="stretch", caption=p.name)
    except Exception as e:
        st.caption(f"⚠️ *Unable to preview `{Path(image_path).name}`: {str(e)}*")


def _render_image_grid(image_paths: List[str], num_cols: int = 3):
    """Render images in a responsive grid layout."""
    for i in range(0, len(image_paths), num_cols):
        cols = st.columns(num_cols)
        for j in range(num_cols):
            if i + j < len(image_paths):
                with cols[j]:
                    _display_image_with_exif_fix(image_paths[i + j])


def _render_video_player(video_path: str):
    """Render video player with playback controls and graceful fallback."""
    try:
        p = Path(video_path)
        if not p.exists():
            st.caption(f"🎬 *Video unavailable: `{p.name}` (not found on disk)*")
            return
        st.video(str(p))
        st.caption(f"🎬 {p.name}")
    except Exception as e:
        st.caption(f"⚠️ *Unable to play `{Path(video_path).name}`: {str(e)}*")


def _parse_media_and_render_in_container(
    content: str, container: Any, is_historical: bool = False
) -> str:
    """
    Parses <img ...> and <video ...> tags from response text.
    Renders them cleanly inside the provided container and returns cleaned text.
    """
    img_pattern = r'<img\s+(?:path|src)=["\']([^"\']+)["\']\s*(?:/>|></img>|>)'
    video_pattern = r'<video\s+(?:path|src)=["\']([^"\']+)["\']\s*(?:/>|></img>|>|</video>)'

    found_imgs = re.findall(img_pattern, content)
    found_videos = re.findall(video_pattern, content)

    # Remove tags and header banners from text
    cleaned_content = re.sub(img_pattern, "", content)
    cleaned_content = re.sub(video_pattern, "", cleaned_content)
    cleaned_content = re.sub(
        r"##\s+(?:Image|Media|Video)\s+References\s*\n*", "", cleaned_content, flags=re.IGNORECASE
    )
    cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content)
    cleaned_content = re.sub(r"[ \t]+\n", "\n", cleaned_content)

    def _resolve_paths(raw_paths: List[str], allowed_exts: set) -> List[str]:
        valid = []
        for p_str in raw_paths:
            # Handle Windows backslashes
            p_clean = p_str.replace("\\", os.sep).replace("/", os.sep)
            p = Path(p_clean)
            if not p.is_absolute():
                p = Path.cwd() / p
            try:
                full_p = p.resolve()
                if full_p.exists() and full_p.suffix.lower() in allowed_exts:
                    valid.append(str(full_p))
                elif full_p.exists():
                    valid.append(str(full_p))
                else:
                    with container:
                        st.caption(f"ℹ️ *Referenced file not found on disk: `{p_clean}`*")
            except Exception:
                continue
        return valid

    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv"}

    valid_images = _resolve_paths(found_imgs, image_exts)
    valid_videos = _resolve_paths(found_videos, video_exts)

    has_media = bool(valid_images or valid_videos)

    if has_media:
        with container:
            if is_historical:
                with st.expander("🖼️ Referenced Media", expanded=False):
                    if valid_images:
                        _render_image_grid(valid_images)
                    for vp in valid_videos:
                        _render_video_player(vp)
            else:
                st.subheader("🖼️ Referenced Media")
                if valid_images:
                    _render_image_grid(valid_images)
                for vp in valid_videos:
                    _render_video_player(vp)

    return cleaned_content.strip()


def parse_and_render_images(content: str, is_historical: bool = False) -> str:
    """Legacy wrapper for backward compatibility."""
    container = st.container()
    return _parse_media_and_render_in_container(content, container, is_historical=is_historical)


def display_greeting():
    """Display welcome message on first load."""
    with st.chat_message("assistant"):
        st.markdown(
            "👋 Hi! I'm **MediaMCP** — your intelligent local media and file assistant.\n\n"
            "I can help you:\n"
            "- 📂 Create, read, organize, or search files across formats (PDF, Office, Code, Data)\n"
            "- 🖼️ Inspect photos, analyze camera EXIF/GPS, or search semantically\n"
            "- 🎬 Preview images, videos, and media files directly in the chat\n"
            "- 🔎 Search the web with live summarization\n\n"
            "👉 Use the **sidebar** to manage conversations, folders, and model settings. What would you like to do?"
        )


def display_chat_history():
    """Render all messages from the active session chat history."""
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                # 1. Assistant reasoning / thinking
                if message.get("reasoning"):
                    with st.expander("🧠 Reasoning & Steps", expanded=False):
                        formatted_reasoning = "\n".join(
                            [f"> {line}" for line in message["reasoning"].strip().split("\n")]
                        )
                        st.markdown(formatted_reasoning)

                # 2. Tool calls
                if message.get("tool_calls"):
                    for tc in message["tool_calls"]:
                        _render_tool_call_expander(tc, expanded=False)

                # 3. Clean text & media containers
                text_container = st.container()
                media_container = st.container()

                cleaned_content = _parse_media_and_render_in_container(
                    message["content"], media_container, is_historical=True
                )

                with text_container:
                    st.markdown(cleaned_content)
            else:
                st.markdown(message["content"])

                # Display user-uploaded files
                if message.get("file_path"):
                    _display_user_files(message["file_path"])


def _display_user_files(file_path: Any):
    """Display files uploaded by user in chat history."""
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
        _render_image_grid(image_paths, num_cols=3)
    if video_paths:
        for vp in video_paths:
            _render_video_player(vp)
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

    chat_store.add_message(
        session_id=active_session_id,
        role="user",
        content=prompt,
        file_path=file_path_value,
        timestamp=now_ts,
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
        tool_calls_container = st.container()
        status_placeholder = st.empty()
        status_placeholder.markdown("🧠 *MediaMCP is thinking...*")
        message_placeholder = st.empty()
        media_container = st.container()

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
            "tool_calls_data": {},
            "final_answer": "",
            "raw_final_answer": "",
            "has_content": False,
            "stopped": False,
        }

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
                    status_placeholder.markdown("🧠 *MediaMCP is thinking...*")

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

                    if content_blocks:
                        for block in content_blocks:
                            b_type = block.get("type")
                            if b_type in ("reasoning", "thought"):
                                r_piece = block.get("text") or block.get("reasoning") or block.get("thought") or ""
                                if r_piece:
                                    state_dict["reasoning"] += r_piece
                                    status_placeholder.markdown("🧠 *Thinking...*")
                                    with reasoning_placeholder.container():
                                        with st.expander("🧠 Reasoning & Steps", expanded=True):
                                            st.markdown(state_dict["reasoning"])
                            elif b_type == "text":
                                t_piece = block.get("text", "")
                                if t_piece:
                                    status_placeholder.markdown("✍️ *MediaMCP is responding...*")
                                    state_dict["raw_final_answer"] += t_piece
                                    state_dict["final_answer"] += t_piece
                                    message_placeholder.markdown(state_dict["final_answer"] + "▌")
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
                            status_placeholder.markdown("🧠 *Thinking...*")
                            with reasoning_placeholder.container():
                                with st.expander("🧠 Reasoning & Steps", expanded=True):
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
                            status_placeholder.markdown("✍️ *MediaMCP is responding...*")
                            state_dict["raw_final_answer"] += text_piece
                            state_dict["final_answer"] += text_piece
                            message_placeholder.markdown(state_dict["final_answer"] + "▌")

                elif event_name == "on_tool_start":
                    tc_id = event.get("run_id") or f"tc_{len(state_dict['tool_calls_data'])}"
                    tc_name = event.get("name", "Tool")
                    tc_args = event.get("data", {}).get("input", {})
                    _debug_log(f"  on_tool_start: name={tc_name}, args={tc_args}, run_id={tc_id}")
                    status_placeholder.markdown(f"🛠️ *Executing: `{tc_name}`...*")

                    tc_placeholder = tool_calls_container.empty()
                    tc_data = {
                        "id": tc_id,
                        "name": tc_name,
                        "args": tc_args,
                        "output": None,
                        "is_error": False,
                        "status": "running",
                        "placeholder": tc_placeholder,
                    }
                    state_dict["tool_calls_data"][tc_id] = tc_data
                    with tc_placeholder.container():
                        _render_tool_call_expander(tc_data, expanded=True)

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
                        with tc_data["placeholder"].container():
                            _render_tool_call_expander(tc_data, expanded=is_err)

                    status_placeholder.markdown("⏳ *Analyzing results...*")

            _debug_log(
                f"stream_agent_response FINISHED loop. Total events: {event_count}, "
                f"raw_final_answer len={len(state_dict['raw_final_answer'])}, "
                f"reasoning len={len(state_dict['reasoning'])}, "
                f"tool_calls count={len(state_dict['tool_calls_data'])}"
            )

        _stream_exception = None
        try:
            agent_loop.run_until_complete(stream_agent_response(state))
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
            else:
                # Rendering / streaming error — save whatever we collected, don't rerun
                err_detail = traceback.format_exc()
                state["raw_final_answer"] += f"\n\n*(Stream error: {type(e).__name__}: {e})*"
                state["final_answer"] += f"\n\n*(Stream error: {type(e).__name__}: {e})*"
                st.warning(f"⚠️ Stream interrupted: `{type(e).__name__}: {e}`")
        finally:
            status_placeholder.empty()

            # Collapse reasoning expander when finished
            if state["reasoning"]:
                formatted_reasoning = "\n".join(
                    [f"> {line}" for line in state["reasoning"].strip().split("\n")]
                )
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
            _debug_log(
                f"FINALLY: raw_final_answer={repr(state['raw_final_answer'])}, "
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

            # Render final cleaned response
            cleaned_final_answer = _parse_media_and_render_in_container(
                final_answer, media_container, is_historical=False
            )
            message_placeholder.markdown(cleaned_final_answer)

            # Persist to in-memory session state
            assistant_msg = {
                "role": "assistant",
                "content": final_answer,
                "reasoning": state["reasoning"],
                "tool_calls": serializable_tool_calls,
                "timestamp": datetime.datetime.now().strftime("%B %d, %Y %H:%M:%S"),
            }
            st.session_state.messages.append(assistant_msg)

            # Persist to SQLite
            active_session_id = st.session_state.get("active_session_id")
            if active_session_id:
                chat_store.add_message(
                    session_id=active_session_id,
                    role="assistant",
                    content=final_answer,
                    reasoning=state["reasoning"],
                    tool_calls=serializable_tool_calls,
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
