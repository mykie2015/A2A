import mesop as me
import asyncio
import os # Import os for environment variable access

from components.header import header
from components.page_scaffold import page_scaffold
from components.page_scaffold import page_frame
from state.state import SettingsState, AppState


def on_selection_change_output_types(e: me.SelectSelectionChangeEvent):
  s = me.state(SettingsState)
  s.output_mime_types = e.values


def settings_page_content():
    """Settings Page Content."""
    settings_state = me.state(SettingsState)
    app_state = me.state(AppState)
    
    # Read OpenAI Base URL from environment for display
    openai_api_base = os.getenv("OPENAI_API_BASE", "Not Set")
    
    with page_scaffold():  # pylint: disable=not-context-manager
        with page_frame():
            with header("Settings", "settings"): pass
            with me.box(
                style=me.Style(
                    display="flex",
                    justify_content="space-between",
                    flex_direction="column",
                    gap=30,
                )
            ):
                # API Key Settings Section
                with me.box(
                    style=me.Style(
                        display="flex",
                        flex_direction="column",
                        margin=me.Margin(bottom=30),
                    )
                ):
                    me.text(
                        "OpenAI Configuration",
                        type="headline-6",
                        style=me.Style(
                            margin=me.Margin(bottom=15),
                            font_family="Google Sans",
                        ),
                    )
                    
                    # Display OpenAI API Key (read-only)
                    me.input(
                        label="OpenAI API Key",
                        value=app_state.api_key,
                        type="password",
                        appearance="outline",
                        readonly=True,
                        style=me.Style(width="400px", margin=me.Margin(bottom=10)),
                    )

                    # Display OpenAI API Base URL (read-only)
                    me.input(
                        label="OpenAI API Base URL",
                        value=openai_api_base,
                        readonly=True,
                        appearance="outline",
                        style=me.Style(width="400px"),
                    )
                
                # Output Types Section
                me.select(
                    label="Supported Output Types",
                    options=[
                        me.SelectOption(label="Image", value="image/*"),
                        me.SelectOption(label="Text (Plain)", value="text/plain"),
                    ],
                    on_selection_change=on_selection_change_output_types,
                    style=me.Style(width=500),
                    multiple=True,
                    appearance="outline",
                    value=settings_state.output_mime_types,
                )


