# sidebar.py
import streamlit as st
import os
from pathlib import Path
from config.settings import (
    load_config,
    save_config,
    DEFAULT_EXCLUSIONS,
    PROVIDER_DEFAULTS,
    fetch_provider_models,
)
from utils.fileops_utils import scan_files
from utils.background_tasks import scan_manager
from utils import chat_store
from utils.deduplication import clean_duplicate_uploads


@st.cache_data(ttl=300, show_spinner=False)
def _get_provider_models(provider: str, api_key: str, base_url: str):
    """Cached call to fetch models directly from provider /models endpoint."""
    return fetch_provider_models(provider, api_key, base_url)


def _dismiss_media_feedback():
    st.session_state.media_feedback = None


def _dismiss_allowed_feedback():
    st.session_state.allowed_feedback = None


def _dismiss_exclusion_feedback():
    st.session_state.exclusion_feedback = None


@st.fragment(run_every="1s")
def _render_scan_progress(expected_task_type: str):
    """Render live scan progress and results without blocking the main app thread."""
    status = scan_manager.get_status()
    if status.get("task_type") != expected_task_type or status.get("status") == "idle":
        return

    st.markdown("---")
    curr_status = status.get("status")
    if curr_status == "running":
        st.markdown(f"**⏳ Scanning in background...**")
        prog = float(status.get("progress") or 0.0)
        desc = status.get("current_step") or "Processing..."
        st.progress(prog, text=desc)
        if st.button("🛑 Cancel Scan", key=f"btn_cancel_scan_{expected_task_type}", width="stretch"):
            scan_manager.cancel()
            st.rerun()
    elif curr_status == "done":
        res = status.get("result") or {}
        if expected_task_type == "media":
            result_md = (
                f"**Scan Complete:**\n"
                f"- Total: `{res.get('total_media_count', 0)}`\n"
                f"- New: `{res.get('new_media_count', 0)}`\n"
                f"- Updated: `{res.get('updated_media_count', 0)}`\n"
                f"- Deleted: `{res.get('deleted_media_count', 0)}`\n"
                f"- Excluded: `{res.get('excluded_media_count', 0)}`"
            )
        else:
            result_md = (
                f"**Scan Complete:**\n"
                f"- Found: `{res.get('found', 0)}`\n"
                f"- New: `{res.get('new', 0)}`\n"
                f"- Updated: `{res.get('updated', 0)}`\n"
                f"- Deleted: `{res.get('deleted', 0)}`\n"
                f"- Excluded: `{res.get('excluded', 0)}`"
            )
        st.success(result_md)
        if st.button("OK", key=f"btn_dismiss_scan_{expected_task_type}", width="stretch"):
            scan_manager.clear()
            st.rerun()
    elif curr_status == "error":
        st.error(f"❌ Scan failed: {status.get('error')}")
        if st.button("OK", key=f"btn_dismiss_scan_err_{expected_task_type}", width="stretch"):
            scan_manager.clear()
            st.rerun()
    elif curr_status == "cancelled":
        st.warning("⚠️ Scan was cancelled by user.")
        if st.button("OK", key=f"btn_dismiss_scan_canc_{expected_task_type}", width="stretch"):
            scan_manager.clear()
            st.rerun()


def _persist_config(cfg):
    """Saves the config object to file."""
    save_config(cfg)


@st.dialog("Custom Instructions", width="large")
def _edit_custom_instructions_dialog():
    cfg = load_config()
    current_instructions = (
        getattr(cfg, "llm_custom_instructions", None)
        or getattr(cfg, "llm_system_prompt", "")
    ).strip()

    st.markdown(
        "Define custom behavioral rules, preferred formatting, tone, or domain guidelines for MediaMCP. "
        "The core persona, MCP tool handling, and media rendering formats are safely managed by the app."
    )
    new_instructions = st.text_area(
        "Custom Instructions",
        value=current_instructions,
        height=320,
        placeholder="e.g. Always respond in concise bullet points.\nPrioritize high-resolution photos.\nExplain technical details simply.",
        key="dialog_custom_instructions_textarea",
        help="These instructions are safely injected into the model prompt to personalize its responses.",
        label_visibility="collapsed",
    )

    col1, col2 = st.columns([1, 1])
    if col1.button("Clear Instructions", key="dialog_btn_clear_custom_inst", width="stretch"):
        cfg.llm_custom_instructions = ""
        cfg.llm_system_prompt = ""
        _persist_config(cfg)
        from client.agent import init_agent
        init_agent.clear()
        st.success("Custom instructions cleared!")
        st.rerun()

    if col2.button("Save Changes", key="dialog_btn_save_custom_inst", type="primary", width="stretch"):
        cfg.llm_custom_instructions = new_instructions.strip()
        cfg.llm_system_prompt = new_instructions.strip()
        _persist_config(cfg)
        from client.agent import init_agent
        init_agent.clear()
        st.success("Custom instructions saved successfully!")
        st.rerun()


_edit_system_prompt_dialog = _edit_custom_instructions_dialog


def manage_sidebar():
    with st.sidebar:
        if "sidebar_view" not in st.session_state:
            st.session_state.sidebar_view = "chats"

        view = st.session_state.sidebar_view

        if view == "chats":
            # Header row: AI settings + Media settings + title
            col_ai, col_media, col_title = st.columns([1, 1, 5], vertical_alignment="center")
            if col_ai.button("🧠", key="btn_toggle_to_ai_settings", help="AI Provider & Model Settings"):
                st.session_state.sidebar_view = "ai_settings"
                st.rerun()
            if col_media.button("📁", key="btn_toggle_to_media_settings", help="Media Paths & Indexing"):
                st.session_state.sidebar_view = "media_settings"
                st.rerun()
            col_title.subheader("💬 Chats")

            # New chat + cleanup
            col_new, col_dedup = st.columns([4, 1], vertical_alignment="center")
            if col_new.button("➕ New Chat", width="stretch", type="primary", key="sidebar_btn_new_chat"):
                new_id = chat_store.create_session("New Chat")
                st.session_state.active_session_id = new_id
                st.session_state.messages = []
                st.rerun()
            if col_dedup.button("🧹", key="sidebar_btn_dedup_uploads", help="Clean duplicate uploads"):
                res = clean_duplicate_uploads("uploads")
                st.toast(f"Cleaned {res.get('duplicates_removed', 0)} duplicates ({res.get('bytes_reclaimed', 0):,} bytes saved)")

            # Search bar
            search_query = st.text_input(
                "Search chats",
                placeholder="🔍 Search conversations...",
                key="sidebar_chat_search",
                label_visibility="collapsed",
            )

            # Load sessions (filtered or all)
            active_id = st.session_state.get("active_session_id")
            if search_query and search_query.strip():
                sessions = chat_store.search_sessions(search_query)
            else:
                sessions = chat_store.list_sessions(active_session_id=active_id)

            if not sessions and not search_query:
                if not active_id or not chat_store.get_session(active_id):
                    new_id = chat_store.create_session("New Chat")
                    st.session_state.active_session_id = new_id
                    active_id = new_id
                sessions = chat_store.list_sessions(active_session_id=active_id)

            count_label = f"{len(sessions)} result{'s' if len(sessions) != 1 else ''}" if search_query else f"{len(sessions)} chat{'s' if len(sessions) != 1 else ''}"
            st.caption(count_label)

            # Session list
            for s in sessions[:50]:
                s_id = s["id"]
                is_active = (s_id == active_id)
                title = (s.get("title") or "New Chat").strip()
                display_title = (title[:24] + "...") if len(title) > 27 else title
                btn_label = f"{'💬' if is_active else '💭'} {display_title}"

                c_link, c_del = st.columns([5.5, 1], vertical_alignment="center")
                if c_link.button(
                    btn_label,
                    key=f"session_link_{s_id}",
                    width="stretch",
                    type="secondary" if is_active else "tertiary",
                    help=f"{title}\nMessages: {s.get('msg_count', 0)}\nUpdated: {s.get('updated_at', '')[:10]}",
                ):
                    st.session_state.active_session_id = s_id
                    st.session_state.messages = chat_store.get_session_messages(s_id)
                    st.rerun()

                if c_del.button("🗑️", key=f"session_del_{s_id}", width="stretch", type="secondary", help=f"Delete '{title}'"):
                    chat_store.delete_session(s_id)
                    if s_id == active_id:
                        rem = chat_store.list_sessions()
                        next_id = rem[0]["id"] if rem else chat_store.create_session("New Chat")
                        st.session_state.active_session_id = next_id
                        st.session_state.messages = chat_store.get_session_messages(next_id)
                    st.rerun()

        elif view == "ai_settings":
            col_back, col_title = st.columns([1, 5], vertical_alignment="center")
            if col_back.button("⬅️", key="btn_back_from_ai", help="Back to Chats"):
                st.session_state.sidebar_view = "chats"
                st.rerun()
            col_title.subheader("🧠 AI Config")

            _render_ai_settings()

        elif view == "media_settings":
            col_back, col_title = st.columns([1, 5], vertical_alignment="center")
            if col_back.button("⬅️", key="btn_back_from_media", help="Back to Chats"):
                st.session_state.sidebar_view = "chats"
                st.rerun()
            col_title.subheader("📁 Paths & Rules")

            _render_media_settings()


def _render_ai_settings():
    config = load_config()

    # Check if the currently configured provider is missing an API key
    provider_keys = list(PROVIDER_DEFAULTS.keys())
    current_provider = config.llm_provider if config.llm_provider in provider_keys else "openai"
    current_idx = provider_keys.index(current_provider)
    current_preset = PROVIDER_DEFAULTS[current_provider]
    current_saved_key = config.get_api_key_for_provider(current_provider) or config.llm_api_key
    missing_key_on_load = current_preset.get("requires_api_key", False) and not current_saved_key

    with st.expander("🔌 AI Provider Setup", expanded=missing_key_on_load):
        def _format_provider(k: str) -> str:
            name = PROVIDER_DEFAULTS[k]["name"]
            return f"{name} (Active)" if k == config.llm_provider else name

        selected_provider = st.selectbox(
            "Select Provider",
            options=provider_keys,
            index=current_idx,
            format_func=_format_provider,
            key="sidebar_provider_setup_select",
        )

        preset = PROVIDER_DEFAULTS[selected_provider]
        is_active_provider = (selected_provider == config.llm_provider)
        requires_key = preset.get("requires_api_key", False)

        # Saved API key specifically connected to this provider
        saved_key = config.get_api_key_for_provider(selected_provider)
        if not saved_key and selected_provider == config.llm_provider:
            saved_key = config.llm_api_key

        def _save_provider_key():
            typed_key = st.session_state.get(f"sidebar_api_key_input_{selected_provider}", "").strip()
            config.set_api_key_for_provider(selected_provider, typed_key)
            _persist_config(config)

        if requires_key:
            key_label = f"{preset['name']} API Key"
            placeholder_text = f"Enter {preset['name']} API key..."

            api_key_val = st.text_input(
                key_label,
                value=saved_key,
                type="password",
                placeholder=placeholder_text,
                key=f"sidebar_api_key_input_{selected_provider}",
                help=f"Directly connected to {preset['name']}. Saved locally in config/config.json",
                autocomplete="off",
                on_change=_save_provider_key,
            )

            if api_key_val:
                st.caption(f"✓ Connected to {preset['name']}")
            else:
                st.caption(f"⚠️ API key required for {preset['name']}")
        else:
            api_key_val = ""
            st.caption(f"✓ {preset['name']} runs locally without an API key.")

        # Base URL
        default_url = preset.get("base_url", "")
        if selected_provider == config.llm_provider:
            initial_url = config.llm_base_url if config.llm_base_url else default_url
        else:
            initial_url = default_url

        if selected_provider in ["ollama", "custom"]:
            base_url_val = st.text_input(
                "Base URL",
                value=initial_url,
                placeholder=default_url or "http://localhost:11434/v1",
                key=f"sidebar_base_url_input_{selected_provider}",
                help=f"Endpoint URL for {preset['name']}",
                autocomplete="off",
            )
        else:
            has_custom_url = bool(initial_url and initial_url != default_url)
            with st.expander("Advanced: Custom Base URL / Proxy", expanded=has_custom_url):
                base_url_val = st.text_input(
                    "Base URL",
                    value=initial_url,
                    placeholder=default_url or "Default official endpoint",
                    key=f"sidebar_base_url_input_{selected_provider}",
                    help="Optional. Leave blank to use default endpoint, or enter custom proxy URL.",
                    autocomplete="off",
                )

        # Provider Activation / Save Button
        prov_btn_label = "Save Provider Settings" if is_active_provider else f"Activate {preset['name']}"
        if st.button(prov_btn_label, type="primary", key="btn_apply_provider", width="stretch"):
            config.llm_provider = selected_provider
            config.llm_base_url = base_url_val.strip()
            config.llm_api_key = api_key_val.strip()
            config.set_api_key_for_provider(selected_provider, api_key_val.strip())
            _persist_config(config)

            from client.agent import init_agent
            init_agent.clear()
            st.success(f"Activated {preset['name']}!")
            st.rerun()

    with st.expander("🧠 AI Model Config", expanded=False):
        active_provider = config.llm_provider if config.llm_provider in provider_keys else "openai"
        active_preset = PROVIDER_DEFAULTS[active_provider]
        st.caption(f"Active Provider: **{active_preset['name']}**")

        active_key = config.get_api_key_for_provider(active_provider) or config.llm_api_key
        active_url = config.llm_base_url if config.llm_base_url else active_preset.get("base_url", "")
        active_requires_key = active_preset.get("requires_api_key", False)

        # Fetch models dynamically for the active provider
        fetched_models = []
        fetch_error = None
        if not active_requires_key or active_key:
            with st.spinner(f"Loading {active_preset['name']} models..."):
                fetched_models, fetch_error = _get_provider_models(
                    active_provider, active_key.strip(), active_url.strip()
                )

        col_lbl, col_btn = st.columns([3, 1], vertical_alignment="bottom")
        col_lbl.markdown("**Model Selection**")
        if col_btn.button("Refresh", key="btn_refresh_models_config", help="Refresh models list from provider"):
            _get_provider_models.clear()
            st.rerun()

        current_configured_model = config.llm_model or ""

        if fetched_models:
            # When no model is saved yet, pick the last (latest) from the sorted list
            latest_model = fetched_models[-1]
            if not current_configured_model:
                current_configured_model = latest_model
                config.llm_model = latest_model
                _persist_config(config)
                from client.agent import init_agent
                init_agent.clear()

            model_options = fetched_models + ["(Custom / Other)"]
            default_model_idx = (
                model_options.index(current_configured_model)
                if current_configured_model in fetched_models
                else len(model_options) - 1  # fall back to "(Custom / Other)"
            )

            chosen_model_option = st.selectbox(
                "Model",
                options=model_options,
                index=default_model_idx,
                key=f"sidebar_model_select_{active_provider}",
                label_visibility="collapsed",
            )

            if chosen_model_option == "(Custom / Other)":
                chosen_model_name = st.text_input(
                    "Custom Model Name",
                    value=current_configured_model if current_configured_model not in fetched_models else "",
                    placeholder="e.g. gpt-4o, llama3.2",
                    key=f"sidebar_custom_model_input_{active_provider}",
                    autocomplete="off",
                )
            else:
                chosen_model_name = chosen_model_option
            st.caption(f"{len(fetched_models)} models loaded from {active_preset['name']}")
        else:
            if active_requires_key and not active_key:
                st.info(f"Configure {active_preset['name']} API key in Provider Setup to load official models.")
            chosen_model_name = st.text_input(
                "Model Name",
                value=current_configured_model,
                placeholder=active_preset.get("default_model") or "model-name",
                key=f"sidebar_manual_model_input_{active_provider}",
                label_visibility="collapsed",
                autocomplete="off",
            )
            st.caption("Using manual model specification")

        if fetch_error:
            st.warning(fetch_error)

        # Custom Instructions Configuration
        custom_instructions = (
            getattr(config, "llm_custom_instructions", None)
            or getattr(config, "llm_system_prompt", "")
        ).strip()
        has_custom = bool(custom_instructions)
        status_text = "Active" if has_custom else "None"
        st.caption(f"Custom Instructions: **{status_text}**")
        if st.button(
            "📝 Custom Instructions",
            key="btn_open_custom_instructions_dialog",
            width="stretch",
            help="Open dialog to set or edit personal guidelines and behavioral rules",
        ):
            _edit_custom_instructions_dialog()

        # Advanced Generation Settings: Temperature
        is_reasoning = any((chosen_model_name or "").lower().startswith(p) for p in ("o1", "o3"))
        with st.expander("⚙️ Advanced Generation Settings", expanded=False):
            st.caption("Fine-tune model generation parameters.")
            if is_reasoning:
                st.info("ℹ️ Temperature is fixed to 1.0 for reasoning models (o1/o3) and cannot be adjusted.")
                temperature_val = 1.0
            else:
                temperature_val = st.slider(
                    "Temperature",
                    min_value=0.0,
                    max_value=2.0,
                    value=float(config.llm_temperature if config.llm_temperature is not None else 0.1),
                    step=0.05,
                    help="Controls randomness. Lower = more focused and deterministic, Higher = more creative.",
                    key="sidebar_temperature_slider",
                )
                st.caption("ℹ️ Note: Reasoning models (e.g. OpenAI o1/o3) ignore or do not support custom temperature.")

        if st.button("Apply Model Settings", type="primary", key="btn_apply_model_config", width="stretch"):
            final_model_name = chosen_model_name.strip() if chosen_model_name else ""
            config.llm_model = final_model_name
            if not is_reasoning:
                config.llm_temperature = float(temperature_val)
            _persist_config(config)

            from client.agent import init_agent
            init_agent.clear()
            st.success(f"Updated model to {final_model_name}!")
            st.rerun()


def _render_media_settings():
    config = load_config()

    if "temp_media_paths" not in st.session_state:
        st.session_state.temp_media_paths = [
            str(p) for p in config.user_media_index_allowed_paths
        ]

    if "temp_exclusion_patterns" not in st.session_state:
        st.session_state.temp_exclusion_patterns = list(config.user_exclusion_paths)

    status_map = {
        "success": st.success,
        "error": st.error,
        "warning": st.warning,
        "info": st.info,
    }

    bg_scan = scan_manager.get_status()
    is_media_active = bg_scan.get("task_type") == "media" and bg_scan.get("status") in ("running", "done", "error", "cancelled")
    is_files_active = bg_scan.get("task_type") == "files" and bg_scan.get("status") in ("running", "done", "error", "cancelled")

    has_media_feedback = bool(st.session_state.get("media_feedback")) or is_media_active
    with st.expander("🖼️ Media Index Paths (Image Search & Scan)", expanded=has_media_feedback):
        st.caption("Directories scanned and indexed for semantic and visual image search.")
        with st.form(key="media_paths_form"):
            paths_to_remove_media = []
            for i, path in enumerate(st.session_state.temp_media_paths):
                col1, col2 = st.columns([4, 1], vertical_alignment="bottom")
                col1.text_input(
                    f"Media Path {i+1}",
                    value=path,
                    key=f"media_path_{i}",
                    disabled=True,
                    label_visibility="collapsed",
                )
                if col2.form_submit_button(
                    "🗑️", key=f"remove_media_{i}", width="stretch"
                ):
                    paths_to_remove_media.append(i)

            if paths_to_remove_media:
                for index in sorted(paths_to_remove_media, reverse=True):
                    st.session_state.temp_media_paths.pop(index)
                st.rerun()

            new_media_path = st.text_input(
                "Add new media path",
                placeholder="Enter a directory to scan...",
                autocomplete="off",
            )
            add_media_path_button = st.form_submit_button("Add Media Path")

            if add_media_path_button:
                if (
                    new_media_path
                    and new_media_path not in st.session_state.temp_media_paths
                    and os.path.isdir(new_media_path)
                ):
                    st.session_state.temp_media_paths.append(new_media_path)
                elif not os.path.isdir(new_media_path):
                    st.warning(f"Path '{new_media_path}' is not a valid directory.")
                else:
                    st.warning(
                        f"Path '{new_media_path}' is already in the list or is empty."
                    )
                st.rerun()

            st.markdown("---")
            col1, col2 = st.columns(2)
            scan_button = col1.form_submit_button(
                "Save & Scan", width="stretch", type="primary"
            )
            save_media_button = col2.form_submit_button("Save Changes", width="stretch")

            if save_media_button or scan_button:
                config.user_media_index_allowed_paths = [
                    Path(p) for p in st.session_state.temp_media_paths
                ]
                _persist_config(config)

                if save_media_button and not scan_button:
                    st.session_state.media_feedback = {
                        "status": "success",
                        "message": "✅ Media Index Paths have been saved!",
                    }
                    st.session_state.pop("temp_media_paths", None)
                    st.rerun()

                if scan_button:
                    valid_paths = [
                        str(p)
                        for p in config.user_media_index_allowed_paths
                        if os.path.isdir(str(p))
                    ]
                    if not valid_paths:
                        st.session_state.media_feedback = {
                            "status": "warning",
                            "message": "No valid media paths to scan.",
                        }
                        st.session_state.pop("temp_media_paths", None)
                        st.rerun()

                    if scan_manager.is_running():
                        st.warning("⚠️ A scan is already running in the background.")
                    else:
                        scan_manager.start_scan("media", valid_paths, prune=True)
                        st.session_state.pop("temp_media_paths", None)
                        st.rerun()

        _render_scan_progress("media")

        if st.session_state.media_feedback:
            fb = st.session_state.media_feedback
            status_map.get(fb.get("status", "info"), st.info)(fb.get("message", ""))
            st.button(
                "OK", key="dismiss_media_feedback", on_click=_dismiss_media_feedback
            )

    with st.expander("📂 Allowed Paths (Agent Access)", expanded=is_files_active):
        st.caption("Folders the AI agent can inspect and operate on.")

        # Built-in Default Uploads Sandbox (Always Allowed)
        from config.settings import DEFAULT_UPLOADS_DIR
        uploads_resolved = str(DEFAULT_UPLOADS_DIR.resolve())
        col_up_path, col_up_badge = st.columns([4, 2], vertical_alignment="center")
        col_up_path.caption(f"🔒 `{uploads_resolved}`")
        col_up_badge.caption("*(Default / Uploads)*")

        # Custom user-configured paths
        user_paths = [str(p) for p in config.user_allowed_paths if str(p) != uploads_resolved]

        if user_paths:
            for i, path in enumerate(user_paths):
                col_path, col_del = st.columns([5, 1], vertical_alignment="center")
                col_path.caption(f"📁 `{path}`")
                if col_del.button("✕", key=f"del_allowed_{i}", help=f"Remove {path}"):
                    config.user_allowed_paths = [
                        p for p in config.user_allowed_paths if str(p) != path
                    ]
                    _persist_config(config)
                    st.rerun()

        new_path = st.text_input(
            "Add folder",
            placeholder="Paste a folder path and press Enter...",
            key="sidebar_add_allowed_path",
            label_visibility="collapsed",
        )
        col_add, col_scan = st.columns(2)
        if col_add.button("➕ Add Path", key="btn_add_allowed_path", width="stretch"):
            new_path_val = new_path.strip() if new_path else ""
            if new_path_val and os.path.exists(new_path_val):
                resolved_new = str(Path(new_path_val).resolve())
                if resolved_new == uploads_resolved:
                    st.toast("Uploads directory is already allowed by default.")
                else:
                    current = [str(p) for p in config.user_allowed_paths]
                    if new_path_val not in current:
                        config.user_allowed_paths.append(Path(new_path_val))
                        _persist_config(config)
                        st.rerun()
                    else:
                        st.toast("Path already added.")
            elif new_path_val:
                st.toast(f"Path does not exist: {new_path_val}")
            else:
                st.toast("Enter a path first.")

        if col_scan.button("🔄 Scan Files", key="btn_scan_allowed", width="stretch", type="primary"):
            valid = [str(p) for p in config.allowed_paths if os.path.isdir(str(p))]
            if not valid:
                st.toast("No valid directories to scan.")
            elif scan_manager.is_running():
                st.toast("A scan is already running.")
            else:
                scan_manager.start_scan("files", valid, prune=True)
                st.rerun()

        _render_scan_progress("files")

    has_exclusion_feedback = bool(st.session_state.get("exclusion_feedback"))
    with st.expander("🚫 Exclusion Patterns (Filter Rules)", expanded=has_exclusion_feedback):
        st.caption("Add patterns to exclude from searches and scans (e.g., '*.tmp', 'backup')")
        st.info(
            f"ℹ️ {len(DEFAULT_EXCLUSIONS)} default exclusions are always active (e.g., node_modules, __pycache__, .git)."
        )

        with st.form(key="exclusion_patterns_form"):
            patterns_to_remove = []
            for i, pattern in enumerate(st.session_state.temp_exclusion_patterns):
                col1, col2 = st.columns([4, 1], vertical_alignment="bottom")
                col1.text_input(
                    f"Pattern {i+1}",
                    value=pattern,
                    key=f"exclusion_pattern_{i}",
                    disabled=True,
                    label_visibility="collapsed",
                )
                if col2.form_submit_button(
                    "🗑️", key=f"remove_exclusion_{i}", width="stretch"
                ):
                    patterns_to_remove.append(i)

            if patterns_to_remove:
                for index in sorted(patterns_to_remove, reverse=True):
                    st.session_state.temp_exclusion_patterns.pop(index)
                st.rerun()

            new_pattern = st.text_input(
                "Add new exclusion pattern",
                placeholder="e.g., '*.tmp', 'backup', 'temp_*'",
                autocomplete="off",
            )
            add_pattern_button = st.form_submit_button("Add Pattern")

            if add_pattern_button:
                if (
                    new_pattern
                    and new_pattern not in st.session_state.temp_exclusion_patterns
                ):
                    st.session_state.temp_exclusion_patterns.append(new_pattern)
                    st.rerun()
                elif not new_pattern:
                    st.warning("Please enter a pattern.")
                else:
                    st.warning(f"Pattern '{new_pattern}' is already in the list.")

            st.markdown("---")
            save_exclusion_button = st.form_submit_button(
                "Save Exclusion Patterns", width="stretch", type="primary"
            )

            if save_exclusion_button:
                try:
                    config.user_exclusion_paths = (
                        st.session_state.temp_exclusion_patterns
                    )
                    _persist_config(config)

                    st.session_state.exclusion_feedback = {
                        "status": "success",
                        "message": f"✅ Saved {len(st.session_state.temp_exclusion_patterns)} user exclusion pattern(s)! ({len(DEFAULT_EXCLUSIONS)} default patterns are always active)",
                    }
                    st.session_state.pop("temp_exclusion_patterns", None)
                    st.rerun()
                except Exception as e:
                    st.session_state.exclusion_feedback = {
                        "status": "error",
                        "message": f"❌ Failed to save exclusions: {e}",
                    }
                    st.rerun()

        st.markdown(
            """
            <details>
            <summary><b>📖 Pattern Examples (click to expand)</b></summary>
            <br/>

            **Wildcard patterns:**
            - `*.log` - Exclude all .log files
            - `*.tmp` - Exclude all .tmp files
            - `temp_*` - Exclude files starting with 'temp_'
            
            **Folder names:**
            - `backup` - Exclude any folder named 'backup'
            - `cache` - Exclude any folder named 'cache'
            - `old_data` - Exclude any folder named 'old_data'
            
            **File names:**
            - `config.local` - Exclude specific file
            - `.env.local` - Exclude specific file
            </details>
            """,
            unsafe_allow_html=True,
        )

        if st.session_state.exclusion_feedback:
            fb = st.session_state.exclusion_feedback
            status_map.get(fb.get("status", "info"), st.info)(fb.get("message", ""))
            st.button(
                "OK",
                key="dismiss_exclusion_feedback",
                on_click=_dismiss_exclusion_feedback,
            )



    with st.expander("📤 Export Image Metadata (CSV)", expanded=False):
        st.caption("Download the entire indexed images database (path, EXIF, GPS, location) as CSV. Exports stream in chunks, so any size is safe.")
        try:
            from utils import database as _db
            with _db.db.cursor() as _cur:
                _cur.execute("SELECT COUNT(*) FROM images")
                _total_images = _cur.fetchone()[0]
            st.caption(f"Indexed images: **{_total_images}**")
        except Exception:
            pass

        f_make = st.text_input("Filter: camera make", key="export_filter_make", placeholder="e.g. Canon (blank = all)", autocomplete="off")
        f_model = st.text_input("Filter: camera model", key="export_filter_model", placeholder="e.g. EOS (blank = all)", autocomplete="off")
        f_country = st.text_input("Filter: country", key="export_filter_country", placeholder="blank = all", autocomplete="off")
        f_city = st.text_input("Filter: city", key="export_filter_city", placeholder="blank = all", autocomplete="off")
        f_gps = st.selectbox(
            "GPS filter",
            options=["All", "With GPS only", "Without GPS only"],
            key="export_filter_gps",
        )
        f_limit_on = st.checkbox("Limit rows", value=False, key="export_limit_on", help="Off = export all matching rows.")
        f_limit = None
        if f_limit_on:
            f_limit = st.number_input("Max rows", min_value=1, value=10000, step=1000, key="export_limit")

        # Live matching-row count for the current filters (cheap COUNT query).
        try:
            from utils.metadata_export import count_images_metadata as _count_meta
            _gps_preview = None
            if f_gps == "With GPS only":
                _gps_preview = True
            elif f_gps == "Without GPS only":
                _gps_preview = False
            _match_count = _count_meta(
                make=(f_make or "").strip() or None,
                model=(f_model or "").strip() or None,
                country=(f_country or "").strip() or None,
                city=(f_city or "").strip() or None,
                has_gps=_gps_preview,
            )
            _shown = min(_match_count, int(f_limit)) if f_limit_on and f_limit else _match_count
            st.caption(f"Matching rows: **{_shown}**" + (" (limited)" if f_limit_on and f_limit and _match_count > int(f_limit) else ""))
        except Exception:
            pass

        if st.button("Generate CSV", key="btn_generate_images_csv", width="stretch"):
            import tempfile as _tf
            from utils.metadata_export import export_images_metadata_to_path as _export_to_path
            _gps = None
            if f_gps == "With GPS only":
                _gps = True
            elif f_gps == "Without GPS only":
                _gps = False
            _limit = int(f_limit) if f_limit_on and f_limit else None
            _tmp_path = None
            try:
                _fd, _tmp_path = _tf.mkstemp(suffix=".csv", prefix="images_metadata_")
                import os as _os
                _os.close(_fd)
                _prog = st.progress(0.0, text="Starting export...")
                def _cb(done, total):
                    try:
                        if total:
                            _prog.progress(min(float(done) / float(total), 1.0), text=f"Exporting... {done}/{total} rows")
                        else:
                            _prog.progress(0.0, text=f"Exporting... {done} rows")
                    except Exception:
                        pass
                _res = _export_to_path(
                    _tmp_path,
                    make=(f_make or "").strip() or None,
                    model=(f_model or "").strip() or None,
                    country=(f_country or "").strip() or None,
                    city=(f_city or "").strip() or None,
                    has_gps=_gps,
                    limit=_limit,
                    progress_callback=_cb,
                )
                _prog.empty()
                # Keep only the file path in session state (not the CSV text)
                # so huge exports don't duplicate memory.
                _old = st.session_state.pop("images_csv_path", None)
                if _old and _old != _res["path"]:
                    try:
                        import os as _os2
                        _os2.unlink(_old)
                    except Exception:
                        pass
                st.session_state["images_csv_path"] = _res["path"]
                st.session_state["images_csv_count"] = _res["row_count"]
                st.rerun()
            except Exception as e:
                try:
                    _prog.empty()
                except Exception:
                    pass
                try:
                    if _tmp_path:
                        import os as _os3
                        _os3.unlink(_tmp_path)
                except Exception:
                    pass
                st.error(f"❌ Export failed: {e}")

        _csv_path = st.session_state.get("images_csv_path")
        _csv_count = st.session_state.get("images_csv_count", 0)
        if _csv_path:
            try:
                import os as _os4
                _size = _os4.path.getsize(_csv_path)
                st.success(f"Ready: {_csv_count} row(s), {_size / 1024:.0f} KB.")
                if _size > 200 * 1024 * 1024:
                    st.warning("File is over 200 MB — downloading may use lots of memory. Consider narrowing the filters.")
                with open(_csv_path, "rb") as _fh:
                    st.download_button(
                        f"⬇️ Download images_metadata ({_csv_count} rows).csv",
                        data=_fh.read(),
                        file_name="images_metadata.csv",
                        mime="text/csv",
                        key="btn_download_images_csv",
                        width="stretch",
                    )
            except Exception as e:
                st.error(f"❌ Export file unavailable, please regenerate: {e}")
                st.session_state.pop("images_csv_path", None)
