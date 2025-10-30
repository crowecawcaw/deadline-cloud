# UI Translations

This directory contains translation files for the Deadline Cloud UI.

## Files

- `deadline_en.ts` - English translation source file (XML format)
- `deadline_en.qm` - Compiled English translation (binary, generated during build)

## Usage

Translation strings are marked in the code with `tr("string")`.

To update the `.ts` file after adding new translatable strings:

1. Manually add new entries to `deadline_en.ts` following the existing format
2. Translations are automatically compiled to `.qm` files during the build process

For strings with placeholders like `%1`, keep the placeholders in translations:
```python
tr("Profile '%1' has an error.").replace("%1", profile_name)
```
```xml
<source>Profile '%1' has an error.</source>
<translation>プロファイル'%1'にエラーがあります。</translation>
```

## Translation Guidelines

Translate:
- UI labels, buttons, window titles
- User-facing error messages
- Status text and tooltips

Do not translate:
- CLI output and terminal messages
- Dynamic/runtime error messages from exceptions
- Log messages
