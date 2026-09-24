# Pi project-root launcher

`launchcommand` is an unchanged copy of `/home/pj/Documents/lamp/launchcommand`, captured September 24, 2026.

To restore it, copy it into the directory containing the `autonomous-os` checkout, then run it there. It locates that sibling checkout relative to itself, so do not run this archived copy from inside `scripts/dev/pi-project-root`. Local runtime configuration and credentials remain outside Git; this script only references their locations.

The script is archived for reproducibility. The SSH workflow uses the already-running systemd user services; it does not run this launcher or install an agent.
