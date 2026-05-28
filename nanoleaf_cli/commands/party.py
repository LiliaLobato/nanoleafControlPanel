"""party command — start with options, stop, disable."""


def run(args, now=None):
    # TODO: implement in task #10
    action = getattr(args, "action", None)
    if action in ("stop", "disable"):
        print("TODO: party stop")
    else:
        print("TODO: party start")
