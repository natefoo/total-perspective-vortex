import os
import tempfile
import time
import unittest

from galaxy.jobs import JobConfiguration

from tpv.commands.test import mock_galaxy
from tpv.rules import gateway

TAGGED_DESTINATIONS = """
destinations:
  base_default:
    abstract: true
    tags: [abstract_only]
  normal:
    runner: local
    tags: [shared, normal_only]
  long:
    runner: local
    destination_name_override: long_dest
    tags: [shared]
  untagged:
    runner: local
  templated_name:
    runner: local
    destination_name_override: "dest_{cores}"
    tags: [shared]
  templated_tag:
    runner: local
    tags: [shared, "tag_{cores}"]
"""

INHERITED_TAGS = """
global:
  default_inherits: base_default
destinations:
  base_default:
    abstract: true
    tags: [from_default]
  plain:
    runner: local
  parent:
    abstract: true
    tags: [from_parent]
  child:
    runner: local
    inherits: parent
  child_with_own_tags:
    runner: local
    inherits: parent
    tags: [own]
"""


class TestMapperHandlerTags(unittest.TestCase):

    def setUp(self):
        gateway.ACTIVE_DESTINATION_MAPPERS = {}
        self.app = mock_galaxy.App(job_conf=os.path.join(os.path.dirname(__file__), "fixtures/job_conf.yml"))
        self.registered = {}
        self.app.job_config.set_dynamic_destination_tags = self._record

    def _record(self, source, tags):
        self.registered[source] = tags

    def _write_config(self, config, path):
        with open(path, "w") as f:
            f.write(config)

    def test_literal_destination_tags_registered(self):
        with tempfile.NamedTemporaryFile("w+t", suffix=".yml") as tmp_file:
            self._write_config(TAGGED_DESTINATIONS, tmp_file.name)
            with self.assertLogs("tpv.core.mapper", level="INFO") as logs:
                gateway.lock_and_load_mapper(self.app, "tpv_dispatcher", [tmp_file.name])
        self.assertEqual(
            self.registered,
            {
                "tpv_dispatcher": {
                    "shared": {"normal", "long_dest", "templated_tag"},
                    "normal_only": {"normal"},
                }
            },
        )
        self.assertTrue(any("'templated_name' has a templated name" in line for line in logs.output), logs.output)
        self.assertTrue(any("'templated_tag' has templated tags" in line for line in logs.output), logs.output)

    @unittest.skipUnless(hasattr(JobConfiguration, "set_dynamic_destination_tags"), "requires Galaxy >= 27.0")
    def test_galaxy_finds_registered_destinations_by_tag(self):
        # use Galaxy's own method instead of the recorder
        del self.app.job_config.set_dynamic_destination_tags
        with tempfile.NamedTemporaryFile("w+t", suffix=".yml") as tmp_file:
            self._write_config(TAGGED_DESTINATIONS, tmp_file.name)
            gateway.lock_and_load_mapper(self.app, "tpv_dispatcher", [tmp_file.name])
        job_config = self.app.job_config
        self.assertEqual(job_config.get_destination_ids_for_tag("shared"), {"normal", "long_dest", "templated_tag"})
        self.assertEqual(job_config.get_destination_ids_for_tag("normal_only"), {"normal"})
        self.assertEqual(job_config.get_destination_ids_for_tag("abstract_only"), set())

        # registering again, as a reload does, replaces what the referrer registered before
        untagged_mapper = gateway.load_destination_mapper([{"destinations": {"normal": {"runner": "local"}}}])
        gateway.register_destination_tags(self.app, "tpv_dispatcher", untagged_mapper)
        self.assertEqual(job_config.get_destination_ids_for_tag("shared"), set())

    def test_galaxy_without_tag_registration_still_loads(self):
        # Galaxy < 27.0 has no set_dynamic_destination_tags
        self.app.job_config = object()
        with tempfile.NamedTemporaryFile("w+t", suffix=".yml") as tmp_file:
            self._write_config(TAGGED_DESTINATIONS, tmp_file.name)
            mapper = gateway.lock_and_load_mapper(self.app, "tpv_dispatcher", [tmp_file.name])
        self.assertIs(gateway.ACTIVE_DESTINATION_MAPPERS["tpv_dispatcher"], mapper)
        self.assertEqual(self.registered, {})

    def test_inherited_destination_tags_registered(self):
        with tempfile.NamedTemporaryFile("w+t", suffix=".yml") as tmp_file:
            self._write_config(INHERITED_TAGS, tmp_file.name)
            gateway.lock_and_load_mapper(self.app, "tpv_dispatcher", [tmp_file.name])
        self.assertEqual(
            self.registered,
            {
                "tpv_dispatcher": {
                    "from_default": {"plain"},
                    "from_parent": {"child"},
                    "own": {"child_with_own_tags"},
                }
            },
        )

    def test_reload_registers_each_referrer(self):
        with tempfile.NamedTemporaryFile("w+t", suffix=".yml") as tmp_file:
            self._write_config(TAGGED_DESTINATIONS, tmp_file.name)
            gateway.lock_and_load_mapper(self.app, "tpv_dispatcher1", [tmp_file.name])
            gateway.lock_and_load_mapper(self.app, "tpv_dispatcher2", [tmp_file.name])
            self.assertEqual(set(self.registered), {"tpv_dispatcher1", "tpv_dispatcher2"})
            self.assertIn("normal_only", self.registered["tpv_dispatcher1"])

            self._write_config(
                """
destinations:
  normal:
    runner: local
    tags: [shared]
""",
                tmp_file.name,
            )
            # wait for reload
            time.sleep(2)
        self.assertEqual(
            self.registered,
            {
                "tpv_dispatcher1": {"shared": {"normal"}},
                "tpv_dispatcher2": {"shared": {"normal"}},
            },
        )
