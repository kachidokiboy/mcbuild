import pytest

from mcbuild import placer
from mcbuild.executor import BuildAborted
from mcbuild.gallery import gallery, sample
from mcbuild.model import Build
from mcbuild.sequence import needs_support
from mcbuild.server import MinecraftServer

from .fake_minecraft import FakeMinecraft


@pytest.mark.parametrize("make", [gallery, lambda: sample("wizard-tower")])
def test_plan_covers_every_block_exactly_once(make):
    build = make()
    commands = placer.plan(build)
    placed = {}
    for c in commands:
        for p in c.positions():
            assert p not in placed, p
            placed[p] = c.block
    assert placed == build.blocks
    assert len(commands) < len(build)


def test_plan_rises_layer_by_layer_with_attachments_last():
    commands = placer.plan(sample("cottage"))
    kinds = [needs_support(c.block) for c in commands]
    first_attachment = kinds.index(True)
    assert all(kinds[first_attachment:])
    ys = [c.p1[1] for c in commands[:first_attachment]]
    assert ys == sorted(ys)
    assert all(c.p1[1] == c.p2[1] for c in commands)  # fills never span layers
    assert not any(c.is_fill for c in commands[first_attachment:])


def test_gallery_needs_far_fewer_commands():
    build = gallery()
    assert len(placer.plan(build)) < len(build) / 2


def test_big_floor_is_one_command():
    b = Build()
    b.fill((0, 0, 0), (49, 0, 49), "oak_planks")
    assert len(placer.plan(b)) == 1


def test_place_builds_the_same_world_with_fills():
    game = FakeMinecraft()
    build = sample("brick-house").translated(0, 64, 0)
    assert placer.place(MinecraftServer(game), build, rate=0) == 0
    assert all(game.block(p) == blk for p, blk in build)
    assert any(c.startswith("fill ") for c in game.log)


def test_failed_fill_falls_back_and_counts_each_block():
    game = FakeMinecraft()
    b = Build()
    b.fill((0, 64, 0), (3, 64, 3), "bogus_block")
    b.fill((10, 64, 0), (13, 64, 3), "stone")
    with pytest.raises(BuildAborted):
        placer.place(MinecraftServer(game), b, rate=0, log=lambda m: None, max_errors=5)
    game2 = FakeMinecraft()
    b2 = Build()
    b2.fill((0, 64, 0), (3, 64, 3), "stone")
    b2.set(9, 64, 9, "bogus_block")
    assert placer.place(MinecraftServer(game2), b2, rate=0, log=lambda m: None) == 1
    assert game2.block((2, 64, 2)) == "minecraft:stone"
