# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

import os
import pytest
import xml.etree.ElementTree as ET

pytest.importorskip("PySide6")

from qtpy.QtWidgets import QApplication  # type: ignore
from qtpy.QtCore import QCoreApplication, QTranslator  # type: ignore
from pathlib import Path


@pytest.mark.skip(reason="Skip until we've added translations")
def test_translation_loading():
    """Test that Japanese translations load and work correctly."""
    app = QApplication.instance() or QApplication([])

    # Set locale to Japanese
    os.environ["LANG"] = "ja_JP.UTF-8"

    # Load Japanese translation
    translator = QTranslator(app)
    translations_dir = (
        Path(__file__).parent.parent.parent.parent.parent
        / "src"
        / "deadline"
        / "client"
        / "ui"
        / "translations"
    )
    qm_file = translations_dir / "deadline_ja_JP.qm"

    assert qm_file.exists(), f"Japanese translation file not found: {qm_file}"
    assert translator.load(str(qm_file)), "Failed to load Japanese translation"

    app.installTranslator(translator)

    # Test translation
    translated = QCoreApplication.translate("ui", "Log in to AWS Deadline Cloud")
    assert translated == "AWS Deadline Cloudにログイン", (
        f"Expected Japanese translation, got: {translated}"
    )


@pytest.mark.skip(reason="Skip until we've added translations")
def test_translation_keys_match_english():
    """Verify all translation files have the same keys as the English translation."""
    translations_dir = (
        Path(__file__).parent.parent.parent.parent.parent
        / "src"
        / "deadline"
        / "client"
        / "ui"
        / "translations"
    )
    en_file = translations_dir / "deadline_en_US.ts"

    # Parse English translation to get all keys
    en_tree = ET.parse(en_file)
    en_keys = {
        msg.find("source").text  # type: ignore[union-attr]
        for msg in en_tree.findall(".//message")
        if msg.find("source") is not None
    }

    # Check all other .ts files
    for ts_file in translations_dir.glob("deadline_*.ts"):
        if ts_file.name == "deadline_en_US.ts":
            continue

        tree = ET.parse(ts_file)
        keys = {
            msg.find("source").text  # type: ignore[union-attr]
            for msg in tree.findall(".//message")
            if msg.find("source") is not None
        }

        missing = en_keys - keys
        extra = keys - en_keys

        assert not missing, f"{ts_file.name} missing keys: {missing}"
        assert not extra, f"{ts_file.name} has extra keys: {extra}"
