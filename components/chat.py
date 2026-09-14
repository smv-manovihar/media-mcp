# Updated chat.py with fix for reasoning_content parsing
import streamlit as st
import datetime
import os
from pathlib import Path
from PIL import Image, ImageOps
import re
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import client.prompts as prompts  # Assuming this import is available; adjust path if needed


def _display_image_with_exif_fix(image_path):
    """
    Load and display image with correct orientation preserved.
    Fixes EXIF orientation issues that cause images to appear rotated.
    """
    try:
        img = Image.open(image_path)

        # Convert palette images with transparency to RGBA to avoid PIL warnings
        if img.mode == "P" and "transparency" in img.info:
            img = img.convert("RGBA")

        # Fix orientation based on EXIF data
        img = ImageOps.exif_transpose(img)

        # Display with caption
        st.image(img, width="stretch", caption=Path(image_path).name)
    except Exception as e:
        st.error(f"❌ Could not load `{Path(image_path).name}`: {str(e)}")


def _render_image_grid(image_paths, num_cols=3):
    """Render images in a responsive grid layout."""
    for i in range(0, len(image_paths), num_cols):
        cols = st.columns(num_cols)
        for j in range(num_cols):
            if i + j < len(image_paths):
                with cols[j]:
                    _display_image_with_exif_fix(image_paths[i + j])


def _parse_images_and_render_in_container(
    content, container, is_historical=False, debug=False
):
    """
    Parse image tags and render them in a specific container.
    Returns cleaned content without image tags.
    """
    img_pattern = r'<img\s+path="([^"]+)"\s*(?:/>|></img>)'
    matches = re.findall(img_pattern, content)

    if debug and matches:
        with container:
            st.info(f"🔍 Debug: Found {len(matches)} image tag(s): {matches}")

    # Remove img tags and clean content
    cleaned_content = re.sub(img_pattern, "", content)
    cleaned_content = re.sub(
        r"##\s+Image\s+References\s*\n*", "", cleaned_content, flags=re.IGNORECASE
    )
    cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content)
    cleaned_content = re.sub(r"[ \t]+\n", "\n", cleaned_content)

    # Render images in the provided container
    if matches:
        with container:
            image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
            valid_paths = []

            for path_str in matches:
                path_str = path_str.replace("\\", os.sep).replace("/", os.sep)
                path = Path(path_str)
                if not path.is_absolute():
                    path = Path.cwd() / path

                try:
                    full_path = path.resolve()

                    if full_path.exists() and full_path.suffix.lower() in image_exts:
                        valid_paths.append(str(full_path))
                    else:
                        st.warning(f"⚠️ Image not found or invalid format: `{path_str}`")
                except Exception as e:
                    st.warning(f"⚠️ Invalid path `{path_str}`: {str(e)}")

            # Render images
            if valid_paths:
                if is_historical:
                    with st.expander("🖼️ Referenced Images", expanded=False):
                        _render_image_grid(valid_paths)
                else:
                    st.subheader("🖼️ Referenced Images")
                    _render_image_grid(valid_paths)
            elif matches:
                error_msg = "ℹ️ No valid images could be loaded from references."
                if is_historical:
                    with st.expander("🖼️ Referenced Images", expanded=False):
                        st.info(error_msg)
                else:
                    st.info(error_msg)

    return cleaned_content.strip()


def parse_and_render_images(content, is_historical=False, debug=False):
    """
    Legacy function that uses st.container() for backward compatibility.
    For new code, use _parse_images_and_render_in_container with explicit container.
    """
    container = st.container()
    return _parse_images_and_render_in_container(
        content, container, is_historical, debug
    )


def display_greeting():
    """Display welcome message on first load."""
    with st.chat_message("assistant"):
        st.markdown(
            "👋 Hi! I'm **MediaMCP** — your local-first media and file assistant.\n\n"
            "I can help you:\n"
            "- 📂 Create, read, move, copy, or delete files and folders\n"
            "- 🖼️ Find images by description or locate visually similar ones\n"
            "- 🔎 Search the web and extract relevant content\n\n"
            "👉 Manage file system permissions from the **sidebar**. What would you like to do?"
        )


def display_chat_history():
    """Render all messages from chat history."""
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            # Display assistant thoughts if present
            if message["role"] == "assistant" and message.get("thoughts"):
                with st.expander("🧠 Thoughts"):
                    st.markdown(message["thoughts"])

            # Parse and display content
            if message["role"] == "assistant":
                # Create containers for text and images within the message context
                text_container = st.container()
                image_container = st.container()

                # Parse images and render in the image container
                cleaned_content = _parse_images_and_render_in_container(
                    message["content"], image_container, is_historical=True
                )

                # Display cleaned text in text container
                with text_container:
                    st.markdown(cleaned_content)
            else:
                st.markdown(message["content"])

            # Display user-uploaded files
            if message["role"] == "user" and message.get("file_path"):
                _display_user_files(message["file_path"])


def _display_user_files(file_path):
    """Display files uploaded by user in chat history."""
    file_paths = file_path if isinstance(file_path, list) else [file_path]

    if not file_paths or file_paths == ["None"]:
        return

    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    image_paths = [p for p in file_paths if Path(p).suffix.lower() in image_exts]
    other_files = [p for p in file_paths if p not in image_paths]

    # Display images in grid
    if image_paths:
        _render_image_grid(image_paths, num_cols=3)

    # Display non-image files with download buttons
    if other_files:
        for file_path in other_files:
            _render_file_download_button(file_path)


def _render_file_download_button(file_path):
    """Render download button for non-image files."""
    filename = os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            data = f.read()

        mime_type = _get_mime_type(filename)
        st.download_button(
            label=f"📄{filename}",
            data=data,
            file_name=filename,
            mime=mime_type,
            width="content",
        )
    except Exception as e:
        st.error(f"❌ Could not prepare `{filename}`: {str(e)}")


def _get_mime_type(filename):
    """Determine MIME type based on file extension."""
    ext = Path(filename).suffix.lower()
    mime_types = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
        ".csv": "text/csv",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
    }
    return mime_types.get(ext, "application/octet-stream")


def handle_user_input(agent, agent_loop):
    """Handle user input and file uploads."""
    user_input = st.chat_input(
        "Ask me anything...",
        accept_file=True,
        file_type=[
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".bmp",
            ".txt",
            ".md",
            ".pdf",
            ".json",
            ".csv",
        ],
    )

    if not user_input:
        return

    files = getattr(user_input, "files", []) or []
    prompt = (getattr(user_input, "text", "") or "").strip()

    # Save uploaded files
    saved_paths = _save_uploaded_files(files) if files else []

    if not prompt and not saved_paths:
        return

    # Handle file-only uploads
    if not prompt:
        if saved_paths:
            st.info("📁 Files uploaded (no text entered)")
            _display_user_files(saved_paths)
        return

    # Add user message to history
    file_path_value = (
        saved_paths[0]
        if len(saved_paths) == 1
        else saved_paths if saved_paths else None
    )

    st.session_state.messages.append(
        {
            "role": "user",
            "content": prompt,
            "timestamp": datetime.datetime.now().strftime("%B %d, %Y %H:%M:%S"),
            "file_path": file_path_value,
        }
    )

    # Display user message
    with st.chat_message("user"):
        st.markdown(prompt)
        if saved_paths:
            _display_user_files(saved_paths)

    # Process with agent
    if agent and agent_loop:
        _process_agent_response(agent, agent_loop)
    else:
        st.warning("⚠️ Agent is not initialized. Please check the console for errors.")


def _save_uploaded_files(files):
    """Save uploaded files to uploads directory with unique names."""
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for uploaded_file in files:
        name = uploaded_file.name
        base, ext = os.path.splitext(name)
        candidate = name
        i = 1

        # Ensure unique filename
        while (uploads_dir / candidate).exists():
            candidate = f"{base} ({i}){ext}"
            i += 1

        path = uploads_dir / candidate
        with open(path, "wb") as f:
            f.write(uploaded_file.read())

        saved_paths.append(str(path.resolve()))

    return saved_paths


def _process_agent_response(agent, agent_loop):
    """Process and stream agent response."""
    with st.chat_message("assistant"):
        with st.expander("🧠 Thoughts", expanded=True):
            thought_container = st.empty()
        message_placeholder = st.empty()
        image_container = st.container()  # Separate container for images

        # Create dynamic prompt template with current date
        date_str = f"Current Date and Time: {datetime.datetime.now()}"
        system_prompt = prompts.sys + f"\n\n{date_str}"
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        with st.spinner("🤔 Thinking..."):
            state = {"thoughts": "", "final_answer": "", "raw_final_answer": ""}

            async def stream_agent_response(state_dict):
                # Prepare message tuples from history
                message_tuples = [
                    (
                        msg["role"],
                        (
                            _format_message_for_agent(msg)
                            if msg["role"] == "user"
                            else msg["content"]
                        ),
                    )
                    for msg in st.session_state.messages
                ]

                # Format messages using the prompt template
                formatted_messages = prompt_template.format_messages(
                    messages=message_tuples
                )

                # Stream the agent
                async for chunk in agent.astream(
                    {"messages": formatted_messages}, stream_mode="values"
                ):
                    if "messages" not in chunk:
                        continue
                    latest_messages = chunk["messages"]
                    if not latest_messages:
                        continue
                    latest_message = latest_messages[-1]

                    if isinstance(latest_message, AIMessage):
                        # Handle reasoning from additional_kwargs
                        if "reasoning_content" in latest_message.additional_kwargs:
                            reasoning = latest_message.additional_kwargs[
                                "reasoning_content"
                            ]
                            formatted_reasoning = "\n".join(
                                [f"> {line}" for line in reasoning.strip().split("\n")]
                            )
                            state_dict[
                                "thoughts"
                            ] += f"**Reasoning:**\n{formatted_reasoning}\n\n"
                            thought_container.markdown(state_dict["thoughts"])

                        # Handle content (final answer or additional content)
                        if latest_message.content:
                            if not latest_message.tool_calls:
                                # Final answer (no tool calls)
                                state_dict["raw_final_answer"] += latest_message.content
                                state_dict["final_answer"] += latest_message.content
                                message_placeholder.markdown(
                                    state_dict["final_answer"] + "▌"
                                )
                            # If content is present with tool calls, it might be additional reasoning, but typically content is empty for tool calls

                        # Handle tool calls
                        if latest_message.tool_calls:
                            for tc in latest_message.tool_calls:
                                tool_call_md = (
                                    f"**Tool Call:**\n"
                                    f"- **Tool:** `{tc['name']}`\n"
                                    f"- **Arguments:** `{tc['args']}`\n\n"
                                )
                                state_dict["thoughts"] += tool_call_md
                                thought_container.markdown(state_dict["thoughts"])

                    elif isinstance(latest_message, ToolMessage):
                        # Handle tool output
                        tool_output = latest_message.content
                        tool_output_md = (
                            f"**Tool Output:**\n```\n{tool_output}\n```\n\n"
                        )
                        state_dict["thoughts"] += tool_output_md
                        thought_container.markdown(state_dict["thoughts"])

            agent_loop.run_until_complete(stream_agent_response(state))

        # Use raw_final_answer which contains the original content with image tags
        final_answer_with_tags = state["raw_final_answer"]

        # Parse images and get cleaned content for display
        cleaned_final_answer = _parse_images_and_render_in_container(
            final_answer_with_tags, image_container, is_historical=False
        )

        # Update message placeholder with cleaned content (without image tags)
        message_placeholder.markdown(cleaned_final_answer)

        # Save to history with ORIGINAL content (includes image tags)
        if final_answer_with_tags or state["thoughts"]:
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": final_answer_with_tags,  # Store original WITH image tags
                    "thoughts": state["thoughts"],
                }
            )


def _format_message_for_agent(msg):
    """Format message for agent consumption."""
    if msg["role"] != "user":
        return msg["content"]

    uploaded = msg.get("file_path")
    uploaded_txt = str(uploaded) if uploaded is not None else "None"

    return (
        f"Message details:\n"
        f"- Sent at: {msg['timestamp']}\n"
        f"- Uploaded file: {uploaded_txt}\n"
        f"---\n"
        f"Message content:\n{msg['content']}"
    )
