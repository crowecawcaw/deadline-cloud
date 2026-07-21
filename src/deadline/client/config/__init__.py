# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
This module encapsulates the configuration of [AWS Deadline Cloud] on a workstation.

By default, configuration is stored in `~/.deadline/config`. If a user sets
the environment variable DEADLINE_CONFIG_FILE_PATH, it is used as the configuration
file path instead.

[AWS Deadline Cloud]: https://aws.amazon.com/deadline-cloud/
"""

__all__ = [
    "DEFAULT_DEADLINE_ENDPOINT_URL",
    "clear_setting",
    "get_best_profile_for_farm",
    "get_setting",
    "get_setting_default",
    "read_config",
    "set_setting",
    "str2bool",
    "write_config",
]

from .config_file import (
    DEFAULT_DEADLINE_ENDPOINT_URL,
    get_best_profile_for_farm,
    get_setting,
    get_setting_default,
    read_config,
    set_setting,
    clear_setting,
    str2bool,
    write_config,
)
