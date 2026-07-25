# Dendritic migration notes

Removed, superseded:
- configuration.nix, configuration-ci.nix -> replaced by hosts.nix (both hosts were byte-identical import lists, now one shared composition)
- modules/zram-writeback.nix -> moved to zram-writeback.nix at root, flake.modules.nixos.zram-writeback
- flake.lock -> not carried over, this flake.nix has two new inputs (flake-parts, import-tree) a stale lock won't have. Run `nix flake lock` to generate a fresh one before anything else.

Renamed:
- packages-custom.nix -> _packages-custom.nix (leading underscore so import-tree's default filter skips it; it returns a plain package attrset, not a flake-parts module, and would fail evaluation if auto-imported). Content is untouched, only the filename and its six call sites changed.

Every other .nix file: same content, wrapped under flake.modules.nixos.<filename-without-extension> = <original function>; nothing inside any file body was rewritten except the two mechanical fixes above. specialArgs (inputs, pkgs-master, pkgs-lw) still flow exactly as before, nothing there was touched.

Not verified by compilation, no Nix evaluator available in the environment that produced this. Before switching:
  nix flake lock
  nix flake check
  sudo nixos-rebuild dry-build --flake .#nixos
