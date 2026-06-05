# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
A UI Widget containing the render setup tab
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qtpy.QtCore import Qt, Signal  # type: ignore
from qtpy.QtWidgets import (  # type: ignore
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .radio_button_widget import HoverRadioButton

from ...api._queue_parameters import _apply_deadline_cloud_v2_channel_migration
from ...config import config_file, get_setting
from .._utils import tr
from ..controllers import DeadlineUIController
from ._deadline_list_combo_boxes import (
    DeadlineFarmListComboBoxController,
    DeadlineQueueListComboBoxController,
    DeadlineStorageProfileListComboBoxController,
)
from .openjd_parameters_widget import OpenJDParametersWidget


class SharedJobSettingsWidget(QWidget):  # pylint: disable=too-few-public-methods
    """
    Widget that holds Job setup shared across all job types.


    Signals:
        parameter_changed: This is sent whenever a parameter value in the widget changes. The message
            is a copy of the parameter definition with the "value" key containing the new value.

    Args:
        initial_settings: dataclass containing the job-specific settings.
        initial_shared_parameter_values: (dict[str, Any]): A dict of parameter values {<name>, <value>, ...}
            to override default queue parameter values from the queue. For example,
            a Rez queue environment may have a default "" for the RezPackages parameter, but a Maya
            submitter would override that default with "maya-2023" or similar.
        use_deadline_cloud_v2_channel (bool): When True, prepend the "deadline-cloud-v2" Conda
            channel ahead of the default "deadline-cloud" channel in the CondaChannels queue
            parameter as it loads, keeping "deadline-cloud" as a fallback. Defaults to False.
        parent: The parent Qt Widget.
    """

    parameter_changed = Signal(dict)

    # Emitted when the queue parameter validity state changes
    valid_parameters = Signal(bool)

    def __init__(
        self,
        *,
        initial_settings: Any,
        initial_shared_parameter_values: dict[str, Any],
        use_deadline_cloud_v2_channel: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent=parent)
        layout = QVBoxLayout(self)

        # This is a dictionary {<name>: <value>} containing values to
        # override the queue parameter defaults.
        self.initial_shared_parameter_values = initial_shared_parameter_values

        self.use_deadline_cloud_v2_channel = use_deadline_cloud_v2_channel

        self.shared_job_properties_box = SharedJobPropertiesWidget(
            initial_settings=initial_settings, parent=self
        )
        layout.addWidget(self.shared_job_properties_box)

        # Breathing room between the Job Properties container and the Deadline Cloud
        # settings group below it.
        layout.addSpacing(12)

        self.deadline_cloud_settings_box = DeadlineCloudSettingsWidget(parent=self)
        layout.addWidget(self.deadline_cloud_settings_box)

        self.queue_parameters_box = OpenJDParametersWidget(
            async_loading_state="Loading Queue Environments...", parent=self
        )
        layout.addWidget(self.queue_parameters_box)
        self.queue_parameters_box.parameter_changed.connect(
            lambda message: self.parameter_changed.emit(message)
        )

        # Track current farm/queue IDs for change detection
        self.farm_id = get_setting("defaults.farm_id")
        self.queue_id = get_setting("defaults.queue_id")
        self.__valid_queue = False

        # Connect to the controller for queue parameters
        self._controller = DeadlineUIController.getInstance()
        self._controller.queue_parameters_updated.connect(
            self._handle_queue_parameters_update, Qt.QueuedConnection
        )
        self._controller.queue_parameters_loading.connect(
            self._handle_queue_parameters_loading, Qt.QueuedConnection
        )
        self._controller.operation_failed.connect(
            self._handle_operation_failed, Qt.QueuedConnection
        )

        # Start initial load
        self._start_load_queue_parameters()

        # Set any "deadline:*" parameters, like deadline:priority.
        # The queue parameters will be set asynchronously by the background thread.
        for name, value in initial_shared_parameter_values.items():
            if name.startswith("deadline:"):
                self.set_parameter_value({"name": name, "value": value})

    def refresh_ui(self, job_settings: Any, load_new_bundle: bool = False):
        # Refresh the job settings in the UI
        self.shared_job_properties_box.refresh_ui(job_settings)

        if load_new_bundle:
            # Update the initial shared parameter values corresponding to the new job bundle
            self.initial_shared_parameter_values = {}
            for parameter in job_settings.parameters:
                if "default" in parameter or "value" in parameter:
                    self.initial_shared_parameter_values[parameter["name"]] = parameter.get(
                        "value", parameter.get("default")
                    )
        self.refresh_queue_parameters(load_new_bundle)

    def refresh_queue_parameters(self, load_new_bundle: bool = False):
        """
        If the default queue id or job bundle has changed, refresh the queue parameters.
        """
        farm_id = get_setting("defaults.farm_id")
        queue_id = get_setting("defaults.queue_id")
        if not farm_id or not queue_id:
            self.queue_parameters_box.rebuild_ui(async_loading_state="")
            return  # If the user has not selected a farm or queue ID, don't try to load
        if (
            self.queue_parameters_box.async_loading_state
            or queue_id != self.queue_id
            or load_new_bundle
        ):
            self.queue_parameters_box.rebuild_ui(
                async_loading_state="Reloading Queue Environments..."
            )
            self._start_load_queue_parameters()

    def _handle_queue_parameters_loading(self, is_loading: bool) -> None:
        """Handle loading state changes from the controller."""
        if is_loading:
            self.queue_parameters_box.rebuild_ui(
                async_loading_state="Loading Queue Environments..."
            )

    def _handle_operation_failed(self, operation_name: str, error: BaseException) -> None:
        """Handle operation failures from the controller."""
        if operation_name == "get_queue_parameters":
            self.__valid_queue = False
            self.valid_parameters.emit(False)
            self.queue_parameters_box.rebuild_ui(
                async_loading_state="Error loading queue environments: {}\n\nError traceback: {}".format(
                    "Invalid queue parameters", error
                )
            )

    def _start_load_queue_parameters(self):
        """
        Triggers the controller to load queue parameters.
        """
        self.farm_id = farm_id = get_setting("defaults.farm_id")
        self.queue_id = queue_id = get_setting("defaults.queue_id")
        if not self.farm_id or not self.queue_id:
            # If the user has not selected a farm or queue ID, don't bother loading
            return
        self._controller.refresh_queue_parameters(farm_id=farm_id, queue_id=queue_id)

    def is_queue_valid(self) -> bool:
        return self.__valid_queue

    def _handle_queue_parameters_update(self, queue_parameters: List) -> None:
        """Handle queue parameters update from the controller."""
        self.__valid_queue = True
        self.valid_parameters.emit(True)
        # Migrate channels before applying submitter overrides, so an explicit override wins.
        if self.use_deadline_cloud_v2_channel:
            _apply_deadline_cloud_v2_channel_migration(queue_parameters)
        # Apply the initial queue parameter values
        for parameter in queue_parameters:
            if parameter["name"] in self.initial_shared_parameter_values:
                parameter["value"] = self.initial_shared_parameter_values[parameter["name"]]
        self.queue_parameters_box.rebuild_ui(parameter_definitions=queue_parameters)

    def update_settings(self, settings):
        self.shared_job_properties_box.update_settings(settings)

    def get_parameters(self):
        """
        Returns a list of OpenJD parameter definition dicts with
        a "value" key filled from the widget.
        """
        queue_parameters = self.queue_parameters_box.get_parameters()
        deadline_shared_job_parameters = self.shared_job_properties_box.get_parameters()

        return queue_parameters + deadline_shared_job_parameters

    def set_parameter_value(self, parameter: dict[str, Any]):
        """
        Given an OpenJD parameter definition with a "value" key,
        set the parameter value in the widget.

        If the parameter value cannot be set, raises a KeyError.
        """
        if parameter["name"].startswith("deadline:"):
            self.shared_job_properties_box.set_parameter_value(parameter)
        else:
            self.queue_parameters_box.set_parameter_value(parameter)


class SharedJobPropertiesWidget(QGroupBox):  # pylint: disable=too-few-public-methods
    """
    UI element to hold top level description components of the submission

    The settings object should be a dataclass with:
      - `name: str`        The name of the Job to submit.
      - `description: str`  The description of the Job to submit.
    """

    def __init__(self, *, initial_settings, parent: Optional[QWidget] = None):
        super().__init__(tr("Job Properties"), parent=parent)

        self._build_ui()
        self.refresh_ui(initial_settings)

    def _build_ui(self):
        self.layout = QFormLayout(self)
        self.layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.sub_name_edit = QLineEdit()
        self.sub_name_edit.setMaxLength(128)
        self.layout.addRow("Name", self.sub_name_edit)

        self.desc_label = QLabel(tr("Description"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setMaxLength(2048)
        self.layout.addRow(self.desc_label, self.desc_edit)

        self.priority_box_label = QLabel(tr("Priority"))
        self.priority_box = QSpinBox(parent=self)
        self.priority_box.setRange(0, 100)
        self.layout.addRow(self.priority_box_label, self.priority_box)

        self.initial_status_box_label = QLabel(tr("Initial state"))
        self.initial_status_box = QComboBox(parent=self)
        self.initial_status_box.addItems(["READY", "SUSPENDED"])
        self.layout.addRow(self.initial_status_box_label, self.initial_status_box)

        self.max_failed_tasks_count_box_label = QLabel(tr("Maximum failed tasks count"))
        self.max_failed_tasks_count_box_label.setToolTip(
            "Maximum number of tasks that can fail before the job will be marked as failed."
        )
        self.max_failed_tasks_count_box = QSpinBox(parent=self)
        self.max_failed_tasks_count_box.setRange(0, 2147483647)
        self.layout.addRow(self.max_failed_tasks_count_box_label, self.max_failed_tasks_count_box)

        self.max_retries_per_task_box_label = QLabel(tr("Maximum retries per task"))
        self.max_retries_per_task_box_label.setToolTip(
            "Maximum number of times that a task will retry before it's marked as failed."
        )
        self.max_retries_per_task_box = QSpinBox(parent=self)
        self.max_retries_per_task_box.setRange(0, 2147483647)
        self.layout.addRow(self.max_retries_per_task_box_label, self.max_retries_per_task_box)

        self.max_worker_count_box_label = QLabel(tr("Maximum worker count"))
        self.max_worker_count_box_label.setToolTip(tr("Maximum worker count of job."))
        self.max_worker_count_box = QSpinBox()
        self.max_worker_count_box.setRange(1, 2147483647)
        self.unlimited_max_worker_count = HoverRadioButton(tr("No max worker count"))
        self.limited_max_worker_count = HoverRadioButton(tr("Set max worker count"))
        self.limited_max_worker_count.toggled.connect(
            self.limited_max_worker_count_radio_button_toggled
        )
        self.max_worker_count_layout = QVBoxLayout()
        self.max_worker_count_layout.addWidget(self.unlimited_max_worker_count)
        self.max_worker_count_layout.addWidget(self.limited_max_worker_count)
        self.max_worker_count_layout.addWidget(self.max_worker_count_box)
        self.layout.addRow(self.max_worker_count_box_label, self.max_worker_count_layout)

    def limited_max_worker_count_radio_button_toggled(self, state):
        """
        Enable the max worker count text box when limited max worker count radio button is enabled.
        """
        self.max_worker_count_box.setHidden(not state)

    def _has_compatible_attr(self, obj, attr_name, expected_type):
        """
        Determine if attribute exists and if the type is correct.
        """
        # DCCs can have anything in the settings object since they define their own dataclass to pass in.
        # Changing what we look for below may cause breaking changes in usage of this library.
        return isinstance(getattr(obj, attr_name, None), expected_type)

    def refresh_ui(self, settings: Any):
        self.sub_name_edit.setText(settings.name)
        self.desc_edit.setText(settings.description)

        # Set all fields with type checking
        self.initial_status_box.setCurrentText(
            settings.initial_status
            if self._has_compatible_attr(settings, "initial_status", str)
            else "READY"
        )
        self.max_failed_tasks_count_box.setValue(
            settings.max_failed_tasks_count
            if self._has_compatible_attr(settings, "max_failed_tasks_count", int)
            else int(get_setting("settings.max_failed_tasks_count"))
        )
        self.max_retries_per_task_box.setValue(
            settings.max_retries_per_task
            if self._has_compatible_attr(settings, "max_retries_per_task", int)
            else int(get_setting("settings.max_retries_per_task"))
        )
        self.priority_box.setValue(
            settings.priority if self._has_compatible_attr(settings, "priority", int) else 50
        )

        has_limited_max_worker_count = (
            (settings.max_worker_count > 0)
            if self._has_compatible_attr(settings, "max_worker_count", int)
            else False
        )
        self.unlimited_max_worker_count.setChecked(not has_limited_max_worker_count)
        self.limited_max_worker_count.setChecked(has_limited_max_worker_count)
        self.max_worker_count_box.setHidden(not has_limited_max_worker_count)
        if has_limited_max_worker_count:
            self.max_worker_count_box.setValue(settings.max_worker_count)

    def set_parameter_value(self, parameter: dict[str, Any]):
        """
        Given an OpenJD parameter definition with a "value" key,
        set the parameter value in the widget.

        If the parameter value cannot be set, raises a KeyError.
        """
        parameter_name = parameter["name"]
        if parameter_name == "deadline:targetTaskRunStatus":
            self.initial_status_box.setCurrentText(parameter["value"])
        elif parameter_name == "deadline:maxFailedTasksCount":
            self.max_failed_tasks_count_box.setValue(parameter["value"])
        elif parameter_name == "deadline:maxRetriesPerTask":
            self.max_retries_per_task_box.setValue(parameter["value"])
        elif parameter_name == "deadline:priority":
            self.priority_box.setValue(parameter["value"])
        elif parameter_name == "deadline:maxWorkerCount":
            if parameter["value"] == -1:
                self.unlimited_max_worker_count.setChecked(True)
                self.limited_max_worker_count.setChecked(False)
                self.max_worker_count_box.setHidden(True)
            else:
                self.unlimited_max_worker_count.setChecked(False)
                self.limited_max_worker_count.setChecked(True)
                self.max_worker_count_box.setHidden(False)
                self.max_worker_count_box.setValue(parameter["value"])
        else:
            raise KeyError(parameter_name)

    def get_parameters(self):
        """
        Returns a list of OpenJD parameter definition dicts with
        a "value" key filled from the widget.
        """
        job_parameters = [
            {
                "name": "deadline:targetTaskRunStatus",
                "type": "STRING",
                "userInterface": {
                    "control": "DROPDOWN_LIST",
                    "label": "Initial state",
                },
                "allowedValues": ["READY", "SUSPENDED"],
                "value": self.initial_status_box.currentText(),
            },
            {
                "name": "deadline:maxFailedTasksCount",
                "description": "Maximum number of Tasks that can fail before the Job will be marked as failed.",
                "type": "INT",
                "userInterface": {
                    "control": "SPIN_BOX",
                    "label": "Maximum failed tasks count",
                },
                "minValue": 0,
                "value": self.max_failed_tasks_count_box.value(),
            },
            {
                "name": "deadline:maxRetriesPerTask",
                "description": "Maximum number of times that a task will retry before it's marked as failed.",
                "type": "INT",
                "userInterface": {
                    "control": "SPIN_BOX",
                    "label": "Maximum retries per task",
                },
                "minValue": 0,
                "value": self.max_retries_per_task_box.value(),
            },
            {
                "name": "deadline:priority",
                "type": "INT",
                "value": self.priority_box.value(),
            },
        ]
        if not self.unlimited_max_worker_count.isChecked():
            job_parameters.append(
                {
                    "name": "deadline:maxWorkerCount",
                    "type": "INT",
                    "value": self.max_worker_count_box.value(),
                }
            )
        return job_parameters

    def update_settings(self, settings):
        """
        Update a given instance of scene settings with updated values.
        """
        # TODO: Extract sticky settings from per-DCC implementation to centralized.
        settings.name = self.sub_name_edit.text()
        settings.description = self.desc_edit.text()

        # Set all fields with type checking
        if self._has_compatible_attr(settings, "initial_status", str):
            settings.initial_status = self.initial_status_box.currentText()

        if self._has_compatible_attr(settings, "max_failed_tasks_count", int):
            settings.max_failed_tasks_count = self.max_failed_tasks_count_box.value()

        if self._has_compatible_attr(settings, "max_retries_per_task", int):
            settings.max_retries_per_task = self.max_retries_per_task_box.value()

        if self._has_compatible_attr(settings, "priority", int):
            settings.priority = self.priority_box.value()

        # Handle `max_worker_count` based on UI selection:
        # Preserve unlimited worker setting by using -1 instead of overriding with spin box value
        if self._has_compatible_attr(settings, "max_worker_count", int):
            if self.unlimited_max_worker_count.isChecked():
                settings.max_worker_count = -1  # -1 denotes no max worker count limits.
            else:
                settings.max_worker_count = self.max_worker_count_box.value()


class DeadlineCloudSettingsWidget(QGroupBox):
    """
    UI component for the AWS Deadline Cloud settings shown on the Shared job
    settings tab.

    Hosts editable farm, queue, and storage-profile selectors. Selecting a
    resource persists it to the config file immediately (there is no Apply
    button on this tab) and cascades: choosing a farm reloads the queue list,
    choosing a queue reloads the storage-profile list. The storage-profile row
    is only shown when the selected queue actually has storage profiles for the
    current OS.

    Signals:
        selection_changed: Emitted after a farm or queue selection is persisted,
            so the parent dialog can reload queue parameters and refresh the
            Submit button state.
    """

    # Emitted after a farm/queue selection is persisted to config.
    selection_changed = Signal()

    # Combo-box placeholder texts that do not represent a real, selectable resource.
    _PLACEHOLDER_TEXTS = ("<refreshing>", "<none selected>")

    def __init__(self, *, parent: Optional[QWidget] = None):
        super().__init__(tr("Deadline Cloud settings"), parent=parent)
        self.deadline_settings: Dict[str, Any] = {"counter": -1}
        self.layout = QFormLayout(self)
        self.layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        # Keep the farm/queue/storage selectors visually close together, and trim the
        # top/bottom padding inside the group box (~50% of the default 11px) so there
        # isn't excess space above and below the selectors.
        self.layout.setVerticalSpacing(4)
        margins = self.layout.contentsMargins()
        self.layout.setContentsMargins(margins.left(), 6, margins.right(), 6)

        # When True, the next queues_updated from the controller is the result of
        # a farm change we initiated, so we continue the cascade to storage profiles.
        self._awaiting_queues_for_cascade = False

        # Track the AWS profile so refresh_setting_controls can detect a profile switch
        # and re-derive the farm/queue selection from the new profile (clearing a stale
        # selection, or selecting the new profile's default) instead of keeping the old.
        self._current_aws_profile = get_setting("defaults.aws_profile_name")

        self._controller = DeadlineUIController.getInstance()

        self._build_ui()

    def _set_enabled_with_label(self, prop_name: str, enabled: bool):
        """Sets the enabled status of a control and its label"""
        getattr(self, prop_name).setEnabled(enabled)
        getattr(self, prop_name + "_label").setEnabled(enabled)

    def _build_ui(self):
        """
        Build the UI for the Deadline settings
        """
        self.farm_box_label = QLabel(tr("Farm"))
        self.farm_box = DeadlineFarmListComboBoxController(parent=self)
        self.layout.addRow(self.farm_box_label, self.farm_box)

        self.queue_box_label = QLabel(tr("Queue"))
        self.queue_box = DeadlineQueueListComboBoxController(parent=self)
        self.layout.addRow(self.queue_box_label, self.queue_box)

        self.storage_profile_box_label = QLabel(tr("Default storage profile"))
        self.storage_profile_box = DeadlineStorageProfileListComboBoxController(parent=self)
        self.layout.addRow(self.storage_profile_box_label, self.storage_profile_box)
        # Hidden until we confirm the selected queue has storage profiles.
        self._set_storage_profile_visible(False)

        # User-initiated selection handlers.
        self.farm_box.box.currentIndexChanged.connect(self._on_farm_changed)
        self.queue_box.box.currentIndexChanged.connect(self._on_queue_changed)
        self.storage_profile_box.box.currentIndexChanged.connect(self._on_storage_profile_changed)

        # Continue the farm -> queue -> storage cascade once the queue list lands.
        self._controller.queues_updated.connect(self._on_queues_list_updated, Qt.QueuedConnection)

        # Recompute storage-profile visibility whenever its list changes.
        model = self.storage_profile_box.box.model()
        model.rowsInserted.connect(self._update_storage_profile_visibility)
        model.rowsRemoved.connect(self._update_storage_profile_visibility)
        model.modelReset.connect(self._update_storage_profile_visibility)
        self._controller.storage_profiles_updated.connect(
            self._update_storage_profile_visibility, Qt.QueuedConnection
        )

    def _set_storage_profile_visible(self, visible: bool) -> None:
        """Show or hide the storage-profile row (label + combo)."""
        self.storage_profile_box_label.setVisible(visible)
        self.storage_profile_box.setVisible(visible)

    def _has_real_storage_profiles(self) -> bool:
        """True if the storage-profile combo holds at least one real profile."""
        box = self.storage_profile_box.box
        for i in range(box.count()):
            if box.itemText(i) in self._PLACEHOLDER_TEXTS:
                continue
            if box.itemData(i):
                return True
        return False

    def _update_storage_profile_visibility(self, *args) -> None:
        """Show the storage-profile row only when real profiles are available.

        Connected to several signals with differing signatures (the combo
        model's rowsInserted/rowsRemoved/modelReset and the controller's
        storage_profiles_updated), so it ignores its arguments.
        """
        self._set_storage_profile_visible(self._has_real_storage_profiles())

    def _resnapshot_config(self) -> None:
        """Refresh each combo's cached config from the just-persisted settings.

        Selections are persisted with the module-level ``set_setting`` (writing the
        global config + disk), but the combos read farm/queue ids for their list
        refreshes from the ``self.config`` snapshot captured at the last
        ``refresh_setting_controls``. After a selection that snapshot is stale, so a
        cascade would list resources for the previous farm/queue. Re-reading here
        keeps the cascade reads in sync with what was just written.
        """
        config = config_file.read_config()
        for box in (self.farm_box, self.queue_box, self.storage_profile_box):
            box.set_config(config)

    def _on_farm_changed(self, index: int) -> None:
        if index < 0:
            return
        farm_id = self.farm_box.box.itemData(index)
        if farm_id is None:
            return
        set_setting("defaults.farm_id", farm_id)
        # The previously selected queue/storage profile belong to the old farm.
        set_setting("defaults.queue_id", "")
        set_setting("settings.storage_profile_id", "")
        self.queue_box.clear_list()
        self.storage_profile_box.clear_list()
        self._set_storage_profile_visible(False)
        # The combos list resources using the farm/queue ids in their cached config
        # snapshot, so re-snapshot from the just-persisted settings before refreshing.
        # Otherwise the cascade would re-list the OLD farm's queues.
        self._resnapshot_config()
        # Cascade: reload the queue list for the new farm, then storage profiles.
        self._awaiting_queues_for_cascade = True
        self.queue_box.refresh_list()
        self.selection_changed.emit()

    def _on_queue_changed(self, index: int) -> None:
        if index < 0:
            return
        queue_id = self.queue_box.box.itemData(index)
        if queue_id is None:
            return
        set_setting("defaults.queue_id", queue_id)
        # The previously selected storage profile belongs to the old queue; clear it
        # so a stale profile can't persist (or be re-shown as a raw id) when the new
        # queue doesn't contain it.
        set_setting("settings.storage_profile_id", "")
        self.storage_profile_box.clear_list()
        self._set_storage_profile_visible(False)
        # Re-snapshot so the storage-profile list is fetched for the new queue, not
        # the queue id in the stale cached snapshot.
        self._resnapshot_config()
        # Cascade: reload storage profiles for the newly selected queue.
        self.storage_profile_box.refresh_list()
        self.selection_changed.emit()

    def _on_storage_profile_changed(self, index: int) -> None:
        if index < 0:
            return
        storage_profile_id = self.storage_profile_box.box.itemData(index)
        # A storage profile does not gate queue parameters or the Submit button,
        # so there is no need to notify the parent here.
        set_setting("settings.storage_profile_id", storage_profile_id or "")

    def _on_queues_list_updated(self, queues_list) -> None:
        """Continue the cascade after a farm change repopulated the queue list."""
        if not self._awaiting_queues_for_cascade:
            return
        self._awaiting_queues_for_cascade = False
        current_queue_id = self.queue_box.box.currentData()
        if current_queue_id:
            set_setting("defaults.queue_id", current_queue_id)
            # Re-snapshot so storage profiles are fetched for this queue.
            self._resnapshot_config()
            self.storage_profile_box.refresh_list()
            self.selection_changed.emit()

    def refresh_setting_controls(self, deadline_authorized):
        """
        Refreshes the controls for UI items that depend on the AWS Deadline Cloud API
        for their values.

        Args:
            deadline_authorized (bool): Should be the result of a call to
                    api.check_deadline_available, for example from
                    an AWS Deadline Cloud Status Widget.
        """
        config = config_file.read_config()

        # Detect an AWS profile switch. Farm/queue/storage settings are profile-scoped,
        # so on a switch the combos must reflect the NEW profile's stored defaults
        # exactly: clear the selection if the new profile has none, or select its
        # default if it has one. Suppressing the lone-resource auto-select for this
        # refresh prevents a single leftover farm/queue from the old profile being
        # re-selected.
        new_profile = get_setting("defaults.aws_profile_name", config=config)
        profile_changed = new_profile != self._current_aws_profile
        self._current_aws_profile = new_profile

        for box in (self.farm_box, self.queue_box, self.storage_profile_box):
            box.set_config(config)
            if profile_changed:
                # Drop the previous profile's list contents so they don't linger when
                # the new profile can't be listed (e.g. it isn't logged in yet).
                box.clear_list()
                # Only arm the one-shot auto-select suppression when a list refresh
                # will actually follow to consume it. If we're not authorized no
                # refresh happens, so arming here would leave the flag set and
                # silently suppress a legitimate auto-select on a later refresh
                # (e.g. after the user logs in to this same profile).
                if deadline_authorized:
                    box.suppress_auto_select_once = True
            box.refresh_selected_id()
        if deadline_authorized:
            self.farm_box.refresh_list()
            self.queue_box.refresh_list()
            self.storage_profile_box.refresh_list()
        self._update_storage_profile_visibility()
