import streamlit as st
import os
from client.agent import init_agent
from utils.image_search_utils import scan_images
from utils.fileops_utils import scan_files
from config.settings import load_config, save_config

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


def _dismiss_media_feedback():
    st.session_state.media_feedback = None


def _dismiss_allowed_feedback():
    st.session_state.allowed_feedback = None


with st.sidebar:
    st.header("⚙️ Manage Paths & Scan")

    config = load_config()

    if "temp_media_paths" not in st.session_state:
        st.session_state.temp_media_paths = list(
            config.get("media_index_allowed_paths", [])
        )
    if "temp_allowed_paths" not in st.session_state:
        st.session_state.temp_allowed_paths = list(config.get("allowed_paths", []))

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
            config["media_index_allowed_paths"] = st.session_state.temp_media_paths
            save_config(config)

            if save_media_button and not scan_button:
                st.session_state.media_feedback = {
                    "status": "success",
                    "message": "✅ Media Index Paths have been saved!",
                }
                st.session_state.pop("temp_media_paths", None)
                st.rerun()

            if scan_button:
                valid_paths = [
                    p for p in config["media_index_allowed_paths"] if os.path.isdir(p)
                ]
                if not valid_paths:
                    st.session_state.media_feedback = {
                        "status": "warning",
                        "message": "No valid media paths to scan.",
                    }
                    st.session_state.pop("temp_media_paths", None)
                    st.rerun()
                # use Streamlit spinner to show execution progress
                try:
                    with st.spinner(
                        "⏳ Scanning... This may take a while depending on paths."
                    ):
                        result = scan_images(valid_paths, silent=True)
                    result_md = (
                        f"**Scan Complete:**\n"
                        f"- Total: `{result['total_media_count']}`\n"
                        f"- New: `{result['new_media_count']}`\n"
                        f"- Updated: `{result['updated_media_count']}`\n"
                        f"- Deleted: `{result['deleted_media_count']}`"
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

    # show media feedback immediately below the media form, with an on_click callback
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
        # --- MODIFIED: Added two buttons for Save and Scan ---
        col1, col2 = st.columns(2)
        scan_allowed_button = col1.form_submit_button(
            "Save & Scan", use_container_width=True, type="primary"
        )
        save_allowed_button = col2.form_submit_button(
            "Save Changes", use_container_width=True
        )

        if save_allowed_button or scan_allowed_button:
            config["allowed_paths"] = st.session_state.temp_allowed_paths
            save_config(config)

            if save_allowed_button and not scan_allowed_button:
                st.session_state.allowed_feedback = {
                    "status": "success",
                    "message": "✅ General Allowed Paths have been saved!",
                }
                st.session_state.pop("temp_allowed_paths", None)
                st.rerun()

            if scan_allowed_button:
                valid_paths = [p for p in config["allowed_paths"] if os.path.isdir(p)]
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
                        result = scan_files(valid_paths, silent=True)
                        result_md = (
                            f"**Scan Complete:**\n"
                            f"- Found: `{result['found']}`\n"
                            f"- New: `{result['new']}`\n"
                            f"- Updated: `{result['updated']}`\n"
                            f"- Deleted: `{result['deleted']}`"
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
                    inputs = {
                        "messages": [
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
