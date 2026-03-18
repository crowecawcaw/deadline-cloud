"""Base class defining the GUI driver interface."""

from abc import ABC, abstractmethod


class BaseDriver(ABC):
    @abstractmethod
    def get_combo_value(self, group_title: str, combo_index: int) -> str:
        """Read the current value of a combo box by group title and index."""

    @abstractmethod
    def set_combo_value(self, group_title: str, combo_index: int, value: str):
        """Open a combo box and select a value by text."""

    @abstractmethod
    def get_checkbox_value(self, group_title: str, checkbox_index: int) -> bool:
        """Read whether a checkbox is checked."""

    @abstractmethod
    def click_checkbox(self, group_title: str, checkbox_index: int):
        """Toggle a checkbox."""

    @abstractmethod
    def click_button(self, name: str):
        """Click a button by its title/name."""

    @abstractmethod
    def window_exists(self) -> bool:
        """Check if the config dialog window is open."""
