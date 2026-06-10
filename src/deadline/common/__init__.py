# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Common utilities for the Deadline Cloud project.
"""

# This package namespace intentionally exposes no names directly. The only public
# (deprecated) surface lives in the ``deadline.common.path_utils`` submodule, which
# declares its own ``__all__``. The empty ``__all__`` here makes that explicit so API
# tooling does not treat incidental imports as public interface.
__all__: list[str] = []
