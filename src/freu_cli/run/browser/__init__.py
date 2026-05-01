from freu_cli.run.browser.base import BrowserAdapter
from freu_cli.run.browser.browser_models import (
    BrowserDomNode,
    BrowserElementState,
    BrowserPageInfo,
    BrowserSessionConfig,
)
from freu_cli.run.browser.extension_adapter import ChromeExtensionBrowserAdapter

__all__ = [
    "BrowserAdapter",
    "BrowserDomNode",
    "BrowserElementState",
    "BrowserPageInfo",
    "BrowserSessionConfig",
    "ChromeExtensionBrowserAdapter",
    "create_browser_adapter",
]


def create_browser_adapter(
    config: BrowserSessionConfig | None = None,
) -> BrowserAdapter:
    return ChromeExtensionBrowserAdapter(config=config)
