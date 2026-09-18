import networkx as nx

from conda_forge_tick.migrators.arch import (
    ArchRebuild,
    LinuxRISCV64,
    _arches_are_configured,
)
from conda_forge_tick.migrators.core import GraphMigrator
from conda_forge_tick.utils import frozen_to_json_friendly


def _payload(name, conda_forge_yml=None, pred=None):
    return {
        "name": name,
        "feedstock_name": name,
        "archived": False,
        "conda-forge.yml": conda_forge_yml or {},
        "pr_info": {"PRed": pred or []},
    }


def _graph(parent_payload):
    # parent -> child, i.e. the child depends on the parent
    gx = nx.DiGraph()
    gx.add_node("lapack", payload=parent_payload)
    gx.add_node("gsl", payload=_payload("gsl"))
    gx.add_edge("lapack", "gsl")
    return gx


def _cycle_graph(outside_parent=False):
    # a and b depend on each other; c depends on a and is outside the cycle.
    # With outside_parent, "ext" is a parent of a from outside the cycle.
    gx = nx.DiGraph()
    for node in ("a", "b", "c"):
        gx.add_node(node, payload=_payload(node))
    gx.add_edge("a", "b")
    gx.add_edge("b", "a")
    gx.add_edge("a", "c")
    if outside_parent:
        gx.add_node("ext", payload=_payload("ext"))
        gx.add_edge("ext", "a")
    return gx


def test_predecessor_migrated_via_build_platform_counts_as_built():
    # mirrors conda-forge/conda-forge-bot: lapack got linux_riscv64 through a
    # rerender rather than a bot PR, so it has no PRed record at all. Its
    # children must not be blocked by that.
    parent = _payload(
        "lapack",
        conda_forge_yml={"build_platform": {"linux_riscv64": "linux_64"}},
    )
    migrator = LinuxRISCV64(graph=_graph(parent), effective_graph=_graph(parent))

    assert not migrator.predecessors_not_yet_built(_payload("gsl"))


def test_predecessor_migrated_via_provider_counts_as_built():
    parent = _payload(
        "lapack",
        conda_forge_yml={"provider": {"linux_riscv64": "default"}},
    )
    migrator = LinuxRISCV64(graph=_graph(parent), effective_graph=_graph(parent))

    assert not migrator.predecessors_not_yet_built(_payload("gsl"))


def test_unmigrated_predecessor_still_blocks():
    # the parent has neither the arch configured nor a PR record -> still blocked
    migrator = LinuxRISCV64(
        graph=_graph(_payload("lapack")),
        effective_graph=_graph(_payload("lapack")),
    )

    assert migrator.predecessors_not_yet_built(_payload("gsl"))


def test_predecessor_with_open_pr_still_blocks():
    parent = _payload("lapack")
    migrator = LinuxRISCV64(graph=_graph(parent), effective_graph=_graph(parent))
    muid = frozen_to_json_friendly(migrator.migrator_uid(parent))
    parent["pr_info"]["PRed"] = [{"data": muid["data"], "PR": {"state": "open"}}]

    assert migrator.predecessors_not_yet_built(_payload("gsl"))


def test_predecessor_with_merged_pr_counts_as_built():
    parent = _payload("lapack")
    migrator = LinuxRISCV64(graph=_graph(parent), effective_graph=_graph(parent))
    muid = frozen_to_json_friendly(migrator.migrator_uid(parent))
    parent["pr_info"]["PRed"] = [{"data": muid["data"], "PR": {"state": "closed"}}]

    assert not migrator.predecessors_not_yet_built(_payload("gsl"))


class _PlainGraphMigrator(GraphMigrator):
    """A graph migrator with no feedstock-visible notion of "already migrated"."""

    migrator_version = 0


def test_default_hook_does_not_treat_config_as_migrated():
    # non-arch migrators keep the old behaviour: only a PRed record counts, so an
    # arch key in conda-forge.yml means nothing to them
    parent = _payload(
        "lapack",
        conda_forge_yml={"build_platform": {"linux_riscv64": "linux_64"}},
    )
    migrator = _PlainGraphMigrator(
        name="test migration",
        graph=_graph(parent),
        effective_graph=_graph(parent),
    )

    assert not migrator.predecessor_already_migrated(parent)
    assert migrator.predecessors_not_yet_built(_payload("gsl"))


def test_arch_rebuild_predecessor_migrated_counts_as_built():
    # the same fix applies to the aarch64 migrator
    parent = _payload(
        "lapack",
        conda_forge_yml={"provider": {"linux_aarch64": "default"}},
    )
    migrator = ArchRebuild(graph=_graph(parent), effective_graph=_graph(parent))

    assert migrator.predecessor_already_migrated(parent)
    assert not migrator.predecessors_not_yet_built(_payload("gsl"))


def test_no_arches_is_not_configured():
    # `all` over an empty iterable is true, so the natural spelling would mark every
    # predecessor as built; defaulting to "done" is the dangerous way to be wrong
    parent = _payload(
        "lapack",
        conda_forge_yml={"build_platform": {"linux_riscv64": "linux_64"}},
    )

    assert not _arches_are_configured(parent, {})


def test_null_conda_forge_yml_sections_do_not_raise():
    # the bot writes null rather than omitting a section, so a plain `.get(..., {})`
    # chain would raise AttributeError on the None
    parent = _payload(
        "lapack",
        conda_forge_yml={"provider": None, "build_platform": None},
    )

    assert not _arches_are_configured(parent, {"linux_riscv64": "linux_64"})


def test_node_in_a_cycle_is_allowed_to_go_first():
    # every member of a dependency cycle has another member upstream of it, so
    # none of them can ever report all parents built. nx.descendants never
    # contains its own source, so the check that was meant to spot this could
    # not fire and the whole cycle stayed parked as "awaiting parents".
    migrator = _PlainGraphMigrator(
        name="test migration",
        graph=_cycle_graph(),
        effective_graph=_cycle_graph(),
    )

    # b is a's only unbuilt parent and is in the cycle with it, so waiting on
    # it is circular and a is allowed to go first
    assert not migrator.predecessors_not_yet_built(_payload("a"))
    assert not migrator.filter_node_not_ready_to_be_migrated(_payload("a"))
    assert migrator.cycle_of["a"] == frozenset({"a", "b"})


def test_node_outside_a_cycle_is_still_blocked_by_unbuilt_parents():
    migrator = _PlainGraphMigrator(
        name="test migration",
        graph=_cycle_graph(),
        effective_graph=_cycle_graph(),
    )

    assert migrator.filter_node_not_ready_to_be_migrated(_payload("c"))
    assert "c" not in migrator.cycle_of


def test_unbuilt_parent_outside_the_cycle_still_blocks():
    # only the predecessors inside our own cycle are circular. "ext" is not in
    # the cycle and has not been built, so a must still wait for it -- letting
    # the whole cycle through regardless would migrate it too early.
    migrator = _PlainGraphMigrator(
        name="test migration",
        graph=_cycle_graph(outside_parent=True),
        effective_graph=_cycle_graph(outside_parent=True),
    )

    assert migrator.predecessors_not_yet_built(_payload("a"))
    assert migrator.filter_node_not_ready_to_be_migrated(_payload("a"))
    assert migrator.cycle_of["a"] == frozenset({"a", "b"})


def test_cycle_detection_survives_construction_from_total_graph():
    # the effective graph is built inside Migrator.__init__, which calls
    # filter_node_not_ready_to_be_migrated before GraphMigrator.__init__ has
    # finished. Cycle state therefore cannot be a plain attribute assigned at
    # the end of __init__ -- doing so raises AttributeError here.
    migrator = _PlainGraphMigrator(
        name="test migration",
        total_graph=_cycle_graph(),
    )

    assert migrator.cycle_of["a"] == frozenset({"a", "b"})
