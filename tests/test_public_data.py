import copy
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("validate_public", pathlib.Path(__file__).with_name("validate_public.py"))
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def catalog():
    return {
        "totals": {"games": 1, "releases": 2, "packages": 1},
        "games": [{"id": "game-a", "title": "A Game", "packageName": "com.example.game", "releases": [
            {"id": "release-a", "index": 0, "releaseName": "A Game 1", "sizeMB": 100},
            {"id": "release-b", "index": 1, "releaseName": "A Game 2", "sizeMB": 110},
        ]}],
    }


class PublicDataTests(unittest.TestCase):
    def test_safe_catalog_retains_all_releases(self):
        self.assertEqual(validator.validate_catalog(catalog()), [])

    def test_nested_download_field_is_rejected_even_if_empty(self):
        data = catalog()
        data["games"][0]["releases"][0]["downloadUrl"] = None
        self.assertTrue(any("Forbidden public field" in error for error in validator.validate_catalog(data)))

    def test_obfuscated_key_spelling_is_still_rejected(self):
        self.assertTrue(validator.privacy_errors({"download_directory_url": None}))
        self.assertTrue(validator.privacy_errors({"client-secret": "not-a-real-value"}))

    def test_private_host_hidden_in_description_is_rejected(self):
        value = "A description " + "https://" + "go." + "srcdl1" + ".xyz/" + "example"
        errors = validator.privacy_errors({"summary": value})
        self.assertTrue(errors)
        self.assertTrue(all(value not in error for error in errors))

    def test_credential_bearing_url_is_rejected_without_printing_value(self):
        value = "https://example.test/path?" + "access_token=" + "not-a-real-value"
        errors = validator.privacy_errors({"url": value})
        self.assertTrue(errors)
        self.assertTrue(all(value not in error for error in errors))

    def test_credential_fragments_and_camel_case_parameters_are_rejected(self):
        for separator, name in (("#", "access_token"), ("#", "clientSecret"), ("?", "clientSecret")):
            value = "https://example.test/path" + separator + name + "=not-a-real-value"
            errors = validator.privacy_errors({"url": value})
            self.assertTrue(errors, name)
            self.assertTrue(all(value not in error for error in errors))

    def test_personal_filesystem_path_is_rejected(self):
        value = "/Users/" + "person" + "/private/catalog.json"
        self.assertTrue(validator.privacy_errors({"source": value}))

    def test_public_profile_url_is_not_a_local_filesystem_path(self):
        self.assertEqual(validator.privacy_errors({"website": "https://example.test/users/profile/"}), [])

    def test_missing_or_repeated_original_entry_is_rejected(self):
        data = catalog()
        data["games"][0]["releases"][1]["index"] = 0
        self.assertIn("Source entry indexes must retain every release exactly once", validator.validate_catalog(data))

    def test_duplicate_stable_id_is_rejected(self):
        data = catalog()
        data["games"][0]["releases"][1]["id"] = "release-a"
        self.assertTrue(any("duplicate release ID" in error for error in validator.validate_catalog(data)))

    def test_incorrect_totals_are_rejected(self):
        data = catalog()
        data["totals"]["releases"] = 1
        self.assertTrue(any("totals.releases" in error for error in validator.validate_catalog(data)))

    def test_validation_does_not_modify_catalog(self):
        data = catalog()
        before = copy.deepcopy(data)
        validator.validate_catalog(data)
        self.assertEqual(data, before)

    def test_private_environment_files_are_rejected_by_shared_filename_guard(self):
        self.assertTrue(validator.private_filename(".env"))
        self.assertTrue(validator.private_filename(".env.production"))
        self.assertFalse(validator.private_filename("catalog.json"))


if __name__ == "__main__":
    unittest.main()
