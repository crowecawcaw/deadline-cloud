# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Generate API snapshot using griffe serialization.

This script loads the deadline package API and serializes it to JSON
for use as a baseline snapshot to detect API changes.
"""

# If griffe not available, run through: hatch run docs:python scripts/generate_api_snapshot.py
import griffe
import json
import sys
from pathlib import Path


# Aliases resolving outside this package are imports (e.g. ``from typing import Any``),
# not part of our API.
TRACKED_PACKAGE = "deadline"


def is_reexported_import(obj: dict) -> bool:
    """True if a member is an imported name rather than our own API.

    Imports like ``from typing import Any`` appear as ``alias`` members whose
    ``target_path`` points outside the tracked package; counting them as API is noise.
    Re-exports of our own package (``from deadline...import JobParameter``) are kept.
    """
    if not isinstance(obj, dict) or obj.get("kind") != "alias":
        return False

    target = obj.get("target_path")
    if not isinstance(target, str) or not target:
        return True  # unresolved alias: most likely an import

    return target != TRACKED_PACKAGE and not target.startswith(f"{TRACKED_PACKAGE}.")


def is_public_interface(name: str, obj: dict = None, all_names: list = None) -> bool:
    """True if a member is public.

    When the module declares ``__all__`` (``all_names``), it is authoritative for named
    members. Submodules bypass it: ``__all__`` does not list submodules by convention,
    yet they are public (e.g. ``deadline.client.api``), so they are judged by their own
    ``__all__`` recursively. Without ``__all__``, fall back to: not underscore-prefixed
    and not an import.
    """
    is_module = isinstance(obj, dict) and obj.get("kind") == "module"

    if all_names is not None and not is_module:
        return name in all_names

    # Heuristic fallback.
    if name.startswith("_"):
        return False
    if is_reexported_import(obj):
        return False
    if obj and isinstance(obj, dict) and "is_public" in obj:
        return obj["is_public"]  # griffe's own flag
    return True


def normalize_paths_in_snapshot(snapshot_data, project_root: Path):
    """Recursively normalize absolute paths to relative paths and remove user-specific info."""
    if isinstance(snapshot_data, dict):
        result = {}
        # ``exports`` is griffe's serialization of ``__all__``; drives member filtering.
        exports = snapshot_data.get("exports")
        all_names = exports if isinstance(exports, list) else None
        for key, value in snapshot_data.items():
            if key == "docstring":
                # Skip docstrings entirely to save space - they're not used in API comparison
                continue
            elif key == "members" and isinstance(value, dict):
                # Filter out private members
                public_members = {}
                for member_name, member_obj in value.items():
                    if is_public_interface(member_name, member_obj, all_names):
                        public_members[member_name] = normalize_paths_in_snapshot(
                            member_obj, project_root
                        )
                result[key] = public_members
            elif key in ["filepath", "source_link", "repository", "remote_url"]:
                # Skip all path-related fields when project_root is None (git reference case)
                # or when we want to avoid user-specific info
                if project_root is None:
                    continue
                elif key == "filepath" and isinstance(value, str):
                    # Convert absolute path to relative path
                    try:
                        abs_path = Path(value)
                        if abs_path.is_absolute() and abs_path.is_relative_to(project_root):
                            result[key] = str(abs_path.relative_to(project_root))
                        else:
                            # Skip non-relative paths entirely to ensure consistent snapshots
                            continue
                    except (ValueError, OSError):
                        # Skip paths that can't be processed to ensure consistent snapshots
                        continue
                elif key == "filepath" and isinstance(value, list):
                    # Convert array of absolute paths to relative paths
                    normalized_paths = []
                    for path_str in value:
                        if isinstance(path_str, str):
                            try:
                                abs_path = Path(path_str)
                                if abs_path.is_absolute() and abs_path.is_relative_to(project_root):
                                    normalized_paths.append(str(abs_path.relative_to(project_root)))
                                # Skip non-relative paths to ensure consistent snapshots
                            except (ValueError, OSError):
                                # Skip paths that can't be processed to ensure consistent snapshots
                                pass
                        else:
                            normalized_paths.append(path_str)
                    result[key] = normalized_paths
                else:
                    # Skip other path-related fields to avoid user-specific info
                    continue
            else:
                result[key] = normalize_paths_in_snapshot(value, project_root)
        return result
    elif isinstance(snapshot_data, list):
        return [normalize_paths_in_snapshot(item, project_root) for item in snapshot_data]
    else:
        return snapshot_data


def generate_api_snapshot(output_path: str = "api_snapshot.json", git_ref: str = None) -> None:
    """Generate API snapshot for the deadline package.

    Args:
        output_path: Path where to save the API snapshot
        git_ref: Optional git reference to load from using griffe.load_git
    """
    try:
        if git_ref:
            print(f"Loading deadline package API from git reference: {git_ref}")
            # Use griffe.load_git to load from specific git reference
            deadline_api = griffe.load_git(
                "deadline",
                ref=git_ref,
                search_paths=["src"],
                resolve_aliases=True,
                resolve_external=False,  # Don't load external packages
                allow_inspection=True,  # Allow fallback to inspection if needed
            )
        else:
            print("Loading deadline package API from current working directory...")
            # Load from current working directory
            deadline_api = griffe.load(
                "deadline",
                search_paths=["src"],
                resolve_aliases=True,
                resolve_external=False,  # Don't load external packages
                allow_inspection=True,  # Allow fallback to inspection if needed
            )

        print(f"Successfully loaded API for: {deadline_api.name}")
        print(f"Found {len(deadline_api.members)} top-level members")

        # Serialize to JSON - use full=False for consistency between git ref and current dir
        print("Serializing API to JSON...")
        print(
            "Using full=False to ensure consistent snapshots between git references and current directory..."
        )
        api_dict = json.loads(deadline_api.as_json(full=False))

        # Normalize file paths to be relative to project root
        print("Normalizing file paths...")
        if git_ref:
            # When using git reference, griffe creates a temporary worktree
            # We'll skip path normalization since paths won't be relative to current dir
            normalized_api_dict = normalize_paths_in_snapshot(api_dict, None)
        else:
            project_root = Path.cwd()
            normalized_api_dict = normalize_paths_in_snapshot(api_dict, project_root)

        # Write to file
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(normalized_api_dict, f, indent=2)

        print(f"API snapshot saved to: {output_file}")
        print(f"Snapshot size: {output_file.stat().st_size:,} bytes")

        # Print summary of what was captured
        print("\nAPI Summary:")
        print(f"  Package: {deadline_api.name}")
        print(f"  Kind: {deadline_api.kind}")
        print(f"  Members: {len(deadline_api.members)}")

        # Show top-level modules
        modules = [
            name for name, member in deadline_api.members.items() if member.kind.value == "module"
        ]
        print(f"  Top-level modules: {len(modules)}")
        for module in sorted(modules)[:10]:  # Show first 10
            print(f"    - {module}")
        if len(modules) > 10:
            print(f"    ... and {len(modules) - 10} more")

    except Exception as e:
        print(f"Error generating API snapshot: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) == 2:
        # One argument - output path for current directory
        # Will default to making a snapshot of the current working changes
        output_path = sys.argv[1]
        generate_api_snapshot(output_path)
    elif len(sys.argv) == 3:
        # Two arguments - output path and git reference
        output_path = sys.argv[1]
        git_ref = sys.argv[2]
        generate_api_snapshot(output_path, git_ref=git_ref)
    else:
        print("Usage: python generate_api_snapshot.py <output_path> [git_ref]")
        print("Examples:")
        print("  python generate_api_snapshot.py /tmp/snapshot.json")
        print("  python generate_api_snapshot.py /tmp/baseline.json mainline")
        sys.exit(1)
