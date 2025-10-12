# sidebar.py
import streamlit as st
import os
from pathlib import Path
from config.settings import load_config, save_config, DEFAULT_EXCLUSIONS
from utils.image_search_utils import scan_images
from utils.fileops_utils import scan_files


def _dismiss_media_feedback():
    st.session_state.media_feedback = None


def _dismiss_allowed_feedback():
    st.session_state.allowed_feedback = None


def _dismiss_exclusion_feedback():
    st.session_state.exclusion_feedback = None


def _persist_config(cfg):
    """Saves the config object to file."""
    save_config(cfg)


def manage_sidebar():
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

        # Media Paths Form
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
                    "🗑️", key=f"remove_media_{i}", width="stretch"
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
                            f"- Excluded: `{result.get('excluded_media_count', 0)}`"
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
            st.button(
                "OK", key="dismiss_media_feedback", on_click=_dismiss_media_feedback
            )

        st.markdown("---")

        # Allowed Paths Form
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
                    "🗑️", key=f"remove_allowed_{i}", width="stretch"
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
            scan_general_files_button = col1.form_submit_button(
                "Save & Scan Files", width="stretch", type="primary"
            )
            save_allowed_button = col2.form_submit_button(
                "Save Changes", width="stretch"
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
                        str(p)
                        for p in config.user_allowed_paths
                        if os.path.isdir(str(p))
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
                                f"- Excluded: `{result.get('excluded', 0)}`"
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

        # Exclusion Patterns Form
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

        if st.session_state.exclusion_feedback:
            fb = st.session_state.exclusion_feedback
            status_map.get(fb.get("status", "info"), st.info)(fb.get("message", ""))
            st.button(
                "OK",
                key="dismiss_exclusion_feedback",
                on_click=_dismiss_exclusion_feedback,
            )
