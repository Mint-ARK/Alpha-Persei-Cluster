"""In-memory notification records must follow the owning operation outcome.

This tracks existing queues/instance records; it does not change their wire
format, infer asset ownership or rerun a committed operation.
"""


FIELDS = (
    "touched_items", "removed_equips", "removed_weapons",
    "deleted_servants", "pending_frames", "touched_chat_stickers",
    "touched_heroes", "added_equips", "added_weapons",
)
DELETION_FIELDS = ("removed_equips", "removed_weapons", "deleted_servants")


def checkpoint_notifications(ctx):
    checkpoint = {}
    for name in FIELDS:
        present = hasattr(ctx, name)
        original = getattr(ctx, name, None)
        value = original.copy() if isinstance(original, (list, set, dict)) else original
        checkpoint[name] = (present, original, value)
    return checkpoint


def restore_notifications(ctx, checkpoint):
    """Discard only changes made after the checkpoint, retaining prior queues."""
    for name, (present, original, value) in checkpoint.items():
        if not present:
            if hasattr(ctx, name):
                delattr(ctx, name)
            continue
        if isinstance(original, list):
            original[:] = value
        elif isinstance(original, (set, dict)):
            original.clear()
            original.update(value)
        else:
            original = value
        setattr(ctx, name, original)


def clear_instance_deletions(ctx):
    """A completed response consumes its instance deletion records once."""
    for name in DELETION_FIELDS:
        value = getattr(ctx, name, None)
        if value is not None:
            value.clear()


def take_pending_frames(ctx):
    if hasattr(ctx, "pop_pending_frames"):
        return ctx.pop_pending_frames()
    pending = getattr(ctx, "pending_frames", None)
    frames = list(pending or [])
    if pending is not None:
        pending.clear()
    return frames
