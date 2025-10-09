import streamlit as st
import os
from pathlib import Path
from client.agent import init_agent
from utils.image_search_utils import scan_images
from utils.fileops_utils import scan_files
from config.settings import load_config, save_config, DEFAULT_EXCLUSIONS
import datetime

st.set_page_config(page_title="MediaMCP", page_icon="🤖")
st.title("📁 MediaMCP")


# --- Assistant Greeting ---
with st.chat_message("assistant"):
    st.markdown(
        "👋 Hi! I'm **MediaMCP** — your local-first media and file assistant.  \n\n"
        "I can help you:\n"
        "- 📂 Create, read, move, copy, or delete files and folders\n"
        "- 🖼️ Find images by description or locate visually similar ones with an image reference\n"
        "- 🔎 Search the web and extract relevant content with smart scraping\n\n"
        "👉 You can manage file system path permissions from the **sidebar**. What would you like to do today?"
    )


agent, agent_loop = init_agent()


if "messages" not in st.session_state:
    st.session_state.messages = []


if "media_feedback" not in st.session_state:
    st.session_state.media_feedback = None
if "allowed_feedback" not in st.session_state:
    st.session_state.allowed_feedback = None
if "exclusion_feedback" not in st.session_state:
    st.session_state.exclusion_feedback = None


def _dismiss_media_feedback():
    st.session_state.media_feedback = None


def _dismiss_allowed_feedback():
    st.session_state.allowed_feedback = None


def _dismiss_exclusion_feedback():
    st.session_state.exclusion_feedback = None


def _persist_config(cfg):
    """Saves the config object to file."""
    save_config(cfg)


with st.sidebar:
    st.header("⚙️ Manage Paths & Scan")

    config = load_config()

    if "temp_media_paths" not in st.session_state:
        st.session_state.temp_media_paths = [
            str(p) for p in config.user_media_index_allowed_paths
        ]

    if "temp_allowed_paths" not in st.session_state:
        st.session_state.temp_allowed_paths = [
            str(p) for p in config.user_allowed_paths
        ]

    if "temp_exclusion_patterns" not in st.session_state:
        st.session_state.temp_exclusion_patterns = list(config.user_exclusion_paths)

    status_map = {
        "success": st.success,
        "error": st.error,
        "warning": st.warning,
        "info": st.info,
    }

    with st.form(key="media_paths_form"):
        st.subheader("Media Index Paths (for scanning)")
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
                "🗑️", key=f"remove_media_{i}", use_container_width=True
            ):
                paths_to_remove_media.append(i)

        if paths_to_remove_media:
            for index in sorted(paths_to_remove_media, reverse=True):
                st.session_state.temp_media_paths.pop(index)
            st.rerun()

        new_media_path = st.text_input(
            "Add new media path", placeholder="Enter a directory to scan..."
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
            "Save & Scan", use_container_width=True, type="primary"
        )
        save_media_button = col2.form_submit_button(
            "Save Changes", use_container_width=True
        )

        if save_media_button or scan_button:
            # --- MODIFIED: Update the 'user_' attribute when saving ---
            # This saves only the paths visible in the UI to the config file.
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
                    for p in config.media_index_allowed_paths
                    if os.path.isdir(str(p))
                ]
                if not valid_paths:
                    st.session_state.media_feedback = {
                        "status": "warning",
                        "message": "No valid media paths to scan.",
                    }
                    st.session_state.pop("temp_media_paths", None)
                    st.rerun()
                try:
                    with st.spinner(
                        "⏳ Scanning... This may take a while depending on paths."
                    ):
                        result = scan_images(valid_paths, prune=True)
                    result_md = (
                        f"**Scan Complete:**\n"
                        f"- Total: `{result.get('total_media_count', 0)}`\n"
                        f"- New: `{result.get('new_media_count', 0)}`\n"
                        f"- Updated: `{result.get('updated_media_count', 0)}`\n"
                        f"- Deleted: `{result.get('deleted_media_count', 0)}`"
                    )
                    st.session_state.media_feedback = {
                        "status": "success",
                        "message": result_md,
                    }
                except Exception as e:
                    st.session_state.media_feedback = {
                        "status": "error",
                        "message": f"❌ Scan failed: {e}",
                    }
                finally:
                    st.session_state.pop("temp_media_paths", None)
                    st.rerun()

    if st.session_state.media_feedback:
        fb = st.session_state.media_feedback
        status_map.get(fb.get("status", "info"), st.info)(fb.get("message", ""))
        st.button("OK", key="dismiss_media_feedback", on_click=_dismiss_media_feedback)

    st.markdown("---")

    with st.form(key="allowed_paths_form"):
        st.subheader("General Allowed Paths (for agent tools)")
        paths_to_remove_allowed = []
        for i, path in enumerate(st.session_state.temp_allowed_paths):
            col1, col2 = st.columns([4, 1], vertical_alignment="bottom")
            col1.text_input(
                f"Allowed Path {i+1}",
                value=path,
                key=f"allowed_path_{i}",
                disabled=True,
                label_visibility="collapsed",
            )
            if col2.form_submit_button(
                "🗑️", key=f"remove_allowed_{i}", use_container_width=True
            ):
                paths_to_remove_allowed.append(i)

        if paths_to_remove_allowed:
            for index in sorted(paths_to_remove_allowed, reverse=True):
                st.session_state.temp_allowed_paths.pop(index)
            st.rerun()

        new_allowed_path = st.text_input(
            "Add new allowed path", placeholder="Enter a general allowed path..."
        )
        add_allowed_path_button = st.form_submit_button("Add Allowed Path")

        if add_allowed_path_button:
            if (
                new_allowed_path
                and new_allowed_path not in st.session_state.temp_allowed_paths
                and os.path.exists(new_allowed_path)
            ):
                st.session_state.temp_allowed_paths.append(new_allowed_path)
            elif not os.path.exists(new_allowed_path):
                st.warning(f"Path '{new_allowed_path}' does not exist.")
            else:
                st.warning(
                    f"Path '{new_allowed_path}' is already in the list or is empty."
                )
            st.rerun()

        st.markdown("---")
        col1, col2 = st.columns(2)
        # Renamed variable for clarity
        scan_general_files_button = col1.form_submit_button(
            "Save & Scan Files", use_container_width=True, type="primary"
        )
        save_allowed_button = col2.form_submit_button(
            "Save Changes", use_container_width=True
        )

        if save_allowed_button or scan_general_files_button:
            config.user_allowed_paths = [
                Path(p) for p in st.session_state.temp_allowed_paths
            ]
            _persist_config(config)

            if save_allowed_button and not scan_general_files_button:
                st.session_state.allowed_feedback = {
                    "status": "success",
                    "message": "✅ General Allowed Paths have been saved!",
                }
                st.session_state.pop("temp_allowed_paths", None)
                st.rerun()

            if scan_general_files_button:
                valid_paths = [
                    str(p) for p in config.allowed_paths if os.path.isdir(str(p))
                ]
                if not valid_paths:
                    st.session_state.allowed_feedback = {
                        "status": "warning",
                        "message": "No valid paths to scan.",
                    }
                    st.session_state.pop("temp_allowed_paths", None)
                    st.rerun()

                try:
                    with st.spinner(
                        "⏳ Scanning general files... This might take a while."
                    ):
                        result = scan_files(valid_paths, prune=True)
                        result_md = (
                            f"**Scan Complete:**\n"
                            f"- Found: `{result.get('found', 0)}`\n"
                            f"- New: `{result.get('new', 0)}`\n"
                            f"- Updated: `{result.get('updated', 0)}`\n"
                            f"- Deleted: `{result.get('deleted', 0)}`"
                        )
                        st.session_state.allowed_feedback = {
                            "status": "success",
                            "message": result_md,
                        }
                except Exception as e:
                    st.session_state.allowed_feedback = {
                        "status": "error",
                        "message": f"❌ Scan failed: {e}",
                    }
                finally:
                    st.session_state.pop("temp_allowed_paths", None)
                    st.rerun()

    if st.session_state.allowed_feedback:
        fb = st.session_state.allowed_feedback
        status_map.get(fb.get("status", "info"), st.info)(fb.get("message", ""))
        st.button(
            "OK", key="dismiss_allowed_feedback", on_click=_dismiss_allowed_feedback
        )

    st.markdown("---")

    # ... (The rest of your code for exclusions and the chat interface remains unchanged)
    # ... (I've omitted it for brevity, but it should be included in your final file)

    with st.form(key="exclusion_patterns_form"):
        st.subheader("🚫 Exclusion Patterns")
        st.caption("Add patterns to exclude from scans (e.g., '*.tmp', 'backup')")

        st.info(
            f"ℹ️ {len(DEFAULT_EXCLUSIONS)} default exclusions are always active (e.g., node_modules, __pycache__, .git). You can add your own patterns below."
        )

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
                "🗑️", key=f"remove_exclusion_{i}", use_container_width=True
            ):
                patterns_to_remove.append(i)

        if patterns_to_remove:
            for index in sorted(patterns_to_remove, reverse=True):
                st.session_state.temp_exclusion_patterns.pop(index)
            st.rerun()

        new_pattern = st.text_input(
            "Add new exclusion pattern", placeholder="e.g., '*.tmp', 'backup', 'temp_*'"
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

        with st.expander("📖 Pattern Examples"):
            st.markdown(
                """
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
            """
            )

        save_exclusion_button = st.form_submit_button(
            "Save Exclusion Patterns", use_container_width=True, type="primary"
        )

        if save_exclusion_button:
            try:
                config.user_exclusion_paths = st.session_state.temp_exclusion_patterns
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

    if st.session_state.exclusion_feedback:
        fb = st.session_state.exclusion_feedback
        status_map.get(fb.get("status", "info"), st.info)(fb.get("message", ""))
        st.button(
            "OK", key="dismiss_exclusion_feedback", on_click=_dismiss_exclusion_feedback
        )


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if (
            message["role"] == "assistant"
            and "thoughts" in message
            and message["thoughts"]
        ):
            with st.expander("🧠 Thoughts"):
                st.markdown(message["thoughts"])
        st.markdown(message["content"])


if prompt := st.chat_input("Ask me anything..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    if agent and agent_loop:
        with st.chat_message("assistant"):
            with st.expander("🧠 Thoughts", expanded=True):
                thought_container = st.empty()
            message_placeholder = st.empty()

            with st.spinner("Thinking..."):
                state = {"thoughts": "", "final_answer": ""}

                async def stream_agent_response(state_dict):
                    current_datetime = datetime.datetime.now().strftime(
                        "%B %d, %Y %H:%M:%S"
                    )
                    inputs = {
                        "messages": [
                            (
                                "system",
                                f"The current date and time is {current_datetime}.",
                            ),
                        ]
                        + [
                            (msg["role"], msg["content"])
                            for msg in st.session_state.messages
                        ]
                    }
                    async for chunk in agent.astream(inputs):
                        if "agent" in chunk:
                            agent_step = chunk.get("agent", {})
                            if messages := agent_step.get("messages"):
                                last_message = messages[-1]
                                if reasoning := last_message.additional_kwargs.get(
                                    "reasoning_content"
                                ):
                                    if (
                                        reasoning.split("\n")[0]
                                        not in state_dict["thoughts"]
                                    ):
                                        formatted_reasoning = "\n".join(
                                            [
                                                f"> {line}"
                                                for line in reasoning.strip().split(
                                                    "\n"
                                                )
                                            ]
                                        )
                                        thought_md = (
                                            f"**Reasoning:**\n{formatted_reasoning}\n\n"
                                        )
                                        state_dict["thoughts"] += thought_md
                                        thought_container.markdown(
                                            state_dict["thoughts"]
                                        )
                                if last_message.tool_calls:
                                    for tc in last_message.tool_calls:
                                        tool_call_md = f"**Tool Call:**\n- **Tool:** `{tc['name']}`\n- **Arguments:** `{tc['args']}`\n\n"
                                        if tool_call_md not in state_dict["thoughts"]:
                                            state_dict["thoughts"] += tool_call_md
                                            thought_container.markdown(
                                                state_dict["thoughts"]
                                            )
                                if not last_message.tool_calls and last_message.content:
                                    state_dict["final_answer"] += last_message.content
                                    message_placeholder.markdown(
                                        state_dict["final_answer"] + "▌"
                                    )
                        elif "tool" in chunk:
                            tool_step = chunk.get("tool", {})
                            if messages := tool_step.get("messages"):
                                tool_output = messages[-1].content
                                tool_output_md = (
                                    f"**Tool Output:**\n```\n{tool_output}\n```\n\n"
                                )
                                if tool_output_md not in state_dict["thoughts"]:
                                    state_dict["thoughts"] += tool_output_md
                                    thought_container.markdown(state_dict["thoughts"])

                agent_loop.run_until_complete(stream_agent_response(state))

                final_answer = state["final_answer"]
                message_placeholder.markdown(final_answer)

                if final_answer or state["thoughts"]:
                    assistant_message = {
                        "role": "assistant",
                        "content": final_answer,
                        "thoughts": state["thoughts"],
                    }
                    st.session_state.messages.append(assistant_message)
    else:
        st.warning("Agent is not initialized. Please check the console for errors.")
