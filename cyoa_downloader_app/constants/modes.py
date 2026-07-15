"""Batch/download mode constants."""

_BATCH_VALID_MODES = {
    "auto", "embed", "zip", "both",
    "icc", "icc_zip", "icc_folder", "website", "website_zip", "website_folder",
    "pure_website", "pure_website_zip", "pure_website_folder",
    "cyoap_vue", "cyoap_vue_zip", "cyoap_vue_folder",
}

_PURE_MODES = {"pure_website", "pure_website_zip", "pure_website_folder"}

_CYOAP_MODES = {"cyoap_vue", "cyoap_vue_zip", "cyoap_vue_folder"}

_WEBSITE_MODES = {"icc", "icc_zip", "icc_folder", "website", "website_zip", "website_folder"}

_FOLDER_MODES = {"icc_folder", "website_folder", "pure_website_folder", "cyoap_vue_folder"}

__all__ = ['_BATCH_VALID_MODES', '_PURE_MODES', '_CYOAP_MODES', '_WEBSITE_MODES', '_FOLDER_MODES']
