# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Combo box widgets for selecting Deadline Cloud resources.

These widgets use the DeadlineUIController for async API operations,
ensuring proper ordering and automatic cancellation of superseded requests.
"""

from configparser import ConfigParser
from typing import Any, List, Optional, TYPE_CHECKING

from qtpy.QtCore import Qt, QSize, Signal
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QStyle,
    QWidget,
)

if TYPE_CHECKING:
    from qtpy.QtCore import SignalInstance

from ...config import config_file
from .._utils import block_signals
from ..controllers import DeadlineUIController


class _DeadlineResourceListComboBoxController(QWidget):
    """
    Base class for combo boxes that select Deadline Cloud resources.

    This class uses the DeadlineUIController for async API operations,
    ensuring proper ordering and automatic cancellation of superseded requests.

    Subclasses should:
    - Call _connect_controller_signals() in __init__ after super().__init__()
    - Implement _get_controller_signal() to return the appropriate signal
    - Implement _get_loading_signal() to return the loading state signal
    - Implement _trigger_refresh() to call the appropriate controller method
    - Implement _get_setting_name() to return the config setting name

    Args:
        resource_name: Display name for the resource type (e.g., "Farm", "Queue")
        parent: Parent widget
    """

    # Emitted when the background refresh catches an exception
    background_exception = Signal(str, BaseException)

    # Emitted with the resource id when the selection changes due to user intent:
    # the user picking an item (Qt's ``activated``) or the lone-resource auto-select.
    # NOT emitted by ``refresh_selected_id``, which only syncs the combo to display
    # the already-persisted value. This split is what lets the host wire selection
    # persistence to genuine selections without programmatic display updates
    # spuriously re-triggering (and clobbering) it.
    user_selected = Signal(str)

    # When True, if nothing is configured yet and the list resolves to exactly one
    # resource, that resource is selected automatically. Subclasses opt in. This lives
    # here - the single point every list refresh funnels through - so auto-select works
    # regardless of what triggered the refresh (dialog open, profile switch, sign-in,
    # manual refresh) without each trigger needing its own hook.
    _auto_select_when_single: bool = False

    def __init__(self, resource_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.resource_name = resource_name
        self.config: Optional[ConfigParser] = None
        self._controller = DeadlineUIController.getInstance()

        self._build_ui()

    def _build_ui(self) -> None:
        """Build the widget UI."""
        self.box = QComboBox(parent=self)
        # ``activated`` fires only on user interaction, never on programmatic
        # setCurrentIndex (unlike ``currentIndexChanged``), so display-sync via
        # refresh_selected_id can't masquerade as a user selection.
        self.box.activated.connect(self._on_user_activated)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.box, stretch=1)

        self.refresh_button = QPushButton("")
        layout.addWidget(self.refresh_button)
        self.refresh_button.setIcon(QApplication.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_button.setFixedSize(QSize(22, 22))
        self.refresh_button.clicked.connect(self.refresh_list)

    def _connect_controller_signals(self) -> None:
        """
        Connect to the controller's signals.

        Subclasses must call this after setting up their specific signal connections.
        """
        # Connect to the data update signal
        self._get_controller_signal().connect(self._handle_list_update, Qt.QueuedConnection)

        # Connect to the loading state signal
        self._get_loading_signal().connect(self._handle_loading_state, Qt.QueuedConnection)

        # Connect to the error signal
        self._controller.operation_failed.connect(
            self._handle_operation_failed, Qt.QueuedConnection
        )

    def _get_controller_signal(self) -> Any:
        """Return the controller signal that provides the resource list."""
        raise NotImplementedError("Subclasses must implement _get_controller_signal")

    def _get_loading_signal(self) -> Any:
        """Return the controller signal that indicates loading state."""
        raise NotImplementedError("Subclasses must implement _get_loading_signal")

    def _trigger_refresh(self) -> None:
        """Trigger a refresh on the controller."""
        raise NotImplementedError("Subclasses must implement _trigger_refresh")

    def _get_setting_name(self) -> str:
        """Return the config setting name for this resource."""
        raise NotImplementedError("Subclasses must implement _get_setting_name")

    def _handle_list_update(self, items_list: List) -> None:
        """Handle the list update from the controller."""
        with block_signals(self.box):
            self.box.clear()
            for item in items_list:
                # Handle both tuple and list formats (Qt signals may convert)
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    name, resource_id = item[0], item[1]
                    self.box.addItem(name, userData=resource_id)

            self.refresh_selected_id()

        # Unified selection rule, applied on every list update regardless of what
        # triggered it (dialog open, profile switch, sign-in, manual refresh):
        #   1. refresh_selected_id (above) selects the stored default if it's in the
        #      list, otherwise falls back to "<none selected>".
        #   2. if nothing is stored and exactly one resource is available, select it.
        # _maybe_auto_select_single self-guards on a configured id, so it never
        # overrides a stored default.
        if self._auto_select_when_single:
            self._maybe_auto_select_single(items_list)

    def _on_user_activated(self, index: int) -> None:
        """Emit ``user_selected`` for a genuine user pick from the dropdown."""
        if index < 0:
            return
        resource_id = self.box.itemData(index)
        if resource_id is None:
            return
        self.user_selected.emit(resource_id)

    def _maybe_auto_select_single(self, items_list: List) -> None:
        """Select the sole available resource if none is configured yet."""
        configured_id = config_file.get_setting(self._get_setting_name(), config=self.config)
        if configured_id:
            return
        real_ids = [
            item[1]
            for item in items_list
            if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]
        ]
        if len(real_ids) != 1:
            return
        index = self.box.findData(real_ids[0])
        if index >= 0:
            # Update the display under block_signals, then emit user_selected
            # explicitly: auto-selecting the lone resource is treated as user intent
            # (it persists + cascades) but ``activated`` does not fire for a
            # programmatic setCurrentIndex, so we signal it ourselves.
            with block_signals(self.box):
                self.box.setCurrentIndex(index)
            self.user_selected.emit(real_ids[0])

    def _handle_loading_state(self, is_loading: bool) -> None:
        """Handle loading state changes."""
        if is_loading:
            # Show refreshing indicator
            selected_id = config_file.get_setting(self._get_setting_name(), config=self.config)
            with block_signals(self.box):
                self.box.clear()
                self.box.addItem("<refreshing>", userData=selected_id)

        self.refresh_button.setEnabled(not is_loading)

    def _handle_operation_failed(self, operation_name: str, error: BaseException) -> None:
        """Handle operation failures from the controller."""
        # Only handle errors for our resource type
        expected_operation = self._get_expected_operation_name()
        if operation_name == expected_operation:
            with block_signals(self.box):
                self.box.clear()
            self.refresh_selected_id()
            self.background_exception.emit(f"Refresh {self.resource_name}s list", error)

    def _get_expected_operation_name(self) -> str:
        """Return the operation name to filter errors by."""
        raise NotImplementedError("Subclasses must implement _get_expected_operation_name")

    def count(self) -> int:
        """Returns the number of items in the combobox."""
        return self.box.count()

    def set_config(self, config: ConfigParser) -> None:
        """Updates the AWS Deadline Cloud config object the control uses."""
        self.config = config
        self._controller.set_config(config)

    def clear_list(self) -> None:
        """
        Fully clears the list. The caller needs to call either
        `refresh_list` or `refresh_selected_id` at a later point.
        """
        with block_signals(self.box):
            self.box.clear()

    def refresh_list(self) -> None:
        """Starts a background refresh of the resource list."""
        self._trigger_refresh()

    def refresh_selected_id(self) -> None:
        """Refreshes the selected id from the config object."""
        selected_id = config_file.get_setting(self._get_setting_name(), config=self.config)
        with block_signals(self.box):
            index = self.box.findData(selected_id)
            if index >= 0:
                self.box.setCurrentIndex(index)
            elif selected_id:
                # User has a configured ID but it's not in the list. This happens when
                # the user has permission to use a resource (e.g., queue) but lacks
                # permission to list resources (e.g., ListFarms). Show the raw ID so
                # they can still see their configured resource.
                self.box.insertItem(0, selected_id, userData=selected_id)
                self.box.setCurrentIndex(0)
            else:
                # No ID selected
                index = self.box.findText("<none selected>")
                if index >= 0:
                    self.box.setCurrentIndex(index)
                else:
                    self.box.insertItem(0, "<none selected>", userData="")
                    self.box.setCurrentIndex(0)


class DeadlineFarmListComboBoxController(_DeadlineResourceListComboBoxController):
    """Combo box for selecting a Deadline Cloud farm."""

    _auto_select_when_single = True

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(resource_name="Farm", parent=parent)
        self._connect_controller_signals()

    def _get_controller_signal(self) -> "SignalInstance":
        return self._controller.farms_updated

    def _get_loading_signal(self) -> "SignalInstance":
        return self._controller.farms_loading

    def _trigger_refresh(self) -> None:
        self._controller.refresh_farms()

    def _get_setting_name(self) -> str:
        return "defaults.farm_id"

    def _get_expected_operation_name(self) -> str:
        return "list_farms"


class DeadlineQueueListComboBoxController(_DeadlineResourceListComboBoxController):
    """Combo box for selecting a Deadline Cloud queue."""

    _auto_select_when_single = True

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(resource_name="Queue", parent=parent)
        self._connect_controller_signals()

    def _get_controller_signal(self) -> "SignalInstance":
        return self._controller.queues_updated

    def _get_loading_signal(self) -> "SignalInstance":
        return self._controller.queues_loading

    def _trigger_refresh(self) -> None:
        farm_id = config_file.get_setting("defaults.farm_id", config=self.config)
        self._controller.refresh_queues(farm_id=farm_id)

    def _get_setting_name(self) -> str:
        return "defaults.queue_id"

    def _get_expected_operation_name(self) -> str:
        return "list_queues"


class DeadlineStorageProfileListComboBoxController(_DeadlineResourceListComboBoxController):
    """Combo box for selecting a Deadline Cloud storage profile."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(resource_name="Storage profile", parent=parent)
        self._connect_controller_signals()

    def _get_controller_signal(self) -> "SignalInstance":
        return self._controller.storage_profiles_updated

    def _get_loading_signal(self) -> "SignalInstance":
        return self._controller.storage_profiles_loading

    def _trigger_refresh(self) -> None:
        farm_id = config_file.get_setting("defaults.farm_id", config=self.config)
        queue_id = config_file.get_setting("defaults.queue_id", config=self.config)
        self._controller.refresh_storage_profiles(farm_id=farm_id, queue_id=queue_id)

    def _get_setting_name(self) -> str:
        return "settings.storage_profile_id"

    def _get_expected_operation_name(self) -> str:
        return "list_storage_profiles"
