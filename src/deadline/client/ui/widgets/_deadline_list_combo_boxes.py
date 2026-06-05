# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Combo box widgets for selecting Deadline Cloud resources.

These widgets use the DeadlineUIController for async API operations,
ensuring proper ordering and automatic cancellation of superseded requests.
"""

from configparser import ConfigParser
from logging import getLogger
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


logger = getLogger(__name__)


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
        # Maps resource_id -> region for resources that carry a region (farms).
        self._region_by_id: dict = {}
        # When set, the next list update skips the lone-resource auto-select. Used on
        # an AWS profile switch, where the selection must reflect the new profile's
        # stored default exactly (cleared if it has none) rather than auto-picking a
        # leftover single resource from the previous profile.
        self.suppress_auto_select_once: bool = False

        self._build_ui()

    def _build_ui(self) -> None:
        """Build the widget UI."""
        self.box = QComboBox(parent=self)
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
        """Handle a wholesale list (re)set from the controller."""
        with block_signals(self.box):
            self.box.clear()
            self._region_by_id = {}
            self._add_items(items_list)
            self.refresh_selected_id()

        # If nothing is configured and exactly one resource is available, select it.
        # Done outside block_signals so currentIndexChanged fires and any connected
        # dialog logic (e.g. cascading to the next resource) reacts as if the user
        # had picked it.
        if self.suppress_auto_select_once:
            # A profile switch must honor the new profile's stored default exactly,
            # so skip auto-select for this single refresh cycle.
            self.suppress_auto_select_once = False
        elif self._auto_select_when_single:
            self._maybe_auto_select_single()

    def _maybe_auto_select_single(self) -> None:
        """Select the sole available resource if none is configured yet.

        Reads the candidates from the combo box itself rather than a passed-in list, so
        it works regardless of whether the box was populated wholesale (``_handle_list_update``)
        or incrementally as regions stream in (``_handle_list_append``).
        """
        configured_id = config_file.get_setting(self._get_setting_name(), config=self.config)
        if configured_id:
            return
        real_ids = [self.box.itemData(i) for i in range(self.box.count()) if self.box.itemData(i)]
        if len(real_ids) != 1:
            return
        index = self.box.findData(real_ids[0])
        if index < 0:
            return
        # Not under block_signals: emitting currentIndexChanged is intentional so connected
        # dialog logic (e.g. cascading to the next resource) reacts as if the user picked it.
        if self.box.currentIndex() != index:
            self.box.setCurrentIndex(index)
        else:
            # The lone item is already current — e.g. Qt auto-selected it at index 0 when it
            # streamed in under block_signals (the incremental append path), so no
            # currentIndexChanged fired. Emit it now so the cascade still runs.
            self.box.currentIndexChanged.emit(index)

    def _add_items(self, items_list: List) -> None:
        """
        Append items to the combo box without clearing it.

        Items may be 2-tuples ``(name, id)`` or 3-tuples ``(label, id, region)``.
        The optional region (3rd element) is recorded so callers can persist a
        resource's region alongside its id (see :meth:`region_for_id`).

        Adding is idempotent on id: if a row for ``id`` already exists (e.g. the same
        id appears twice in one batch, or a region response is duplicated/retried), its
        label is updated in place rather than adding a second row.

        After adding, the box is re-sorted by label so the list has a stable,
        alphabetized order regardless of the order items arrive in (e.g. when farms
        stream in per region out of order). For farms the label is region-first, so
        this naturally orders by region then name.
        """
        for item in items_list:
            # Handle both tuple and list formats (Qt signals may convert)
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                name, resource_id = item[0], item[1]
                existing = self.box.findData(resource_id)
                if existing >= 0:
                    # Same id already present: update its label instead of duplicating.
                    self.box.setItemText(existing, name)
                else:
                    self.box.addItem(name, userData=resource_id)
                if len(item) >= 3 and item[2]:
                    self._region_by_id[resource_id] = item[2]
        self._sort_items_by_label()

    def _sort_items_by_label(self) -> None:
        """
        Re-orders the combo box rows alphabetically by label (case-insensitive),
        keeping each row's id (userData) attached. Ties break by id for determinism.

        Callers invoke this inside a ``block_signals`` block; the region map is keyed by
        id so it is unaffected by reordering, and selection is restored by id afterward.
        """
        rows = [(self.box.itemText(i), self.box.itemData(i)) for i in range(self.box.count())]
        ordered = sorted(rows, key=lambda row: (row[0].casefold(), str(row[1] or "")))
        if ordered == rows:
            return
        self.box.clear()
        for text, resource_id in ordered:
            self.box.addItem(text, userData=resource_id)

    def _handle_list_append(self, items_list: List) -> None:
        """
        Append items to the combo box as they stream in, preserving selection.

        Used for incremental (multi-region) updates: each call adds more items
        without clearing the box, so a slow source does not clobber items already
        shown. The user's current selection (by id) is preserved across appends,
        and a placeholder ``<refreshing>``/``<none selected>`` row left from the
        loading state is removed once real items arrive.
        """
        if not items_list:
            return
        # Capture the selection to restore it after appending (avoids flicker / reset).
        selected_id = self.box.currentData()
        with block_signals(self.box):
            # Drop any placeholder row(s) from the loading state before real data lands.
            for placeholder in ("<refreshing>", "<none selected>"):
                index = self.box.findText(placeholder)
                if index >= 0:
                    self.box.removeItem(index)
            # Drop any raw-id placeholder rows (inserted by refresh_selected_id when the
            # configured id wasn't listed yet) for ids about to arrive with real labels,
            # so a configured farm doesn't appear twice once its region streams in.
            for item in items_list:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    existing = self.box.findData(item[1])
                    if existing >= 0:
                        self.box.removeItem(existing)
            self._add_items(items_list)

            # Restore the prior selection if it's still present; otherwise fall back
            # to the configured id so a streamed-in farm gets selected when it arrives.
            restore_id = selected_id or config_file.get_setting(
                self._get_setting_name(), config=self.config
            )
            index = self.box.findData(restore_id) if restore_id else -1
            if index >= 0:
                self.box.setCurrentIndex(index)

    def region_for_id(self, resource_id: str) -> str:
        """Returns the region recorded for a resource id, or '' if none/unknown."""
        return self._region_by_id.get(resource_id, "")

    def current_region(self) -> str:
        """Returns the region of the currently-selected item, or '' if none/unknown."""
        return self.region_for_id(self.box.currentData())

    def _handle_loading_state(self, is_loading: bool) -> None:
        """Handle loading state changes."""
        if is_loading:
            # Show refreshing indicator
            selected_id = config_file.get_setting(self._get_setting_name(), config=self.config)
            with block_signals(self.box):
                self.box.clear()
                self.box.addItem("<refreshing>", userData=selected_id)
        elif self._auto_select_when_single:
            # Loading finished. For incrementally-populated lists (e.g. farms streaming in
            # per region via _handle_list_append), this is the point at which the full set
            # is known, so auto-select a lone resource here. Done outside block_signals so
            # currentIndexChanged fires and the dialog's cascade reacts naturally.
            self._maybe_auto_select_single()

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
        # Incremental multi-region population: each region's farms are appended as
        # they arrive, and per-region failures surface a non-blocking warning.
        self._controller.farms_appended.connect(self._handle_list_append, Qt.QueuedConnection)
        self._controller.farm_region_warning.connect(
            self._handle_region_warning, Qt.QueuedConnection
        )

    def _handle_region_warning(self, region: str, error: BaseException) -> None:
        """
        Surface a per-region ListFarms failure non-blockingly (no modal).

        Per the streaming requirements, a single region failing must not block farms
        from regions that succeeded and must not pop a modal per failure. We log a
        warning and set it as the combo box tooltip so the user can see something went
        wrong without an interrupting dialog.
        """
        logger.warning("Failed to list farms in region %s: %s", region, error)
        self.box.setToolTip(f"Some regions could not be listed (e.g. {region}: {error})")

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
