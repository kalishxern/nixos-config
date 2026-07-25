# Dendritic migration notes

Removed, superseded:
- configuration.nix, configuration-ci.nix -> replaced by hosts.nix (both hosts were byte-identical import lists, now one shared composition)
- modules/zram-writeback.nix -> moved to zram-writeback.nix at root, flake.modules.nixos.zram-writeback
- flake.lock -> not carried over, this flake.nix has a new input (flake-parts) a stale lock won't have. Run `nix flake lock` to generate a fresh one before anything else.

Renamed:
- packages-custom.nix -> _packages-custom.nix (leading underscore, the flake.nix tree-walker explicitly skips underscore-prefixed names; it returns a plain package attrset, not a flake-parts module, and would fail evaluation if auto-imported). Content is untouched, only the filename and its six call sites changed.
- flake.nix does NOT use the import-tree library. First version did, and calling it against a directory that also holds flake.nix tried to re-evaluate flake.nix as a module and threw `error: undefined variable 'inputs'`. Replaced with a small manual `builtins.readDir` walker in flake.nix itself that excludes exactly "flake.nix" by name and anything starting with "_", recursively. No external dependency for this part, so there is one fewer input to reason about.

Every other .nix file: same content, wrapped under flake.modules.nixos.<filename-without-extension> = <original function>; nothing inside any file body was rewritten except the two mechanical fixes above. specialArgs (inputs, pkgs-master, pkgs-lw) still flow exactly as before, nothing there was touched.

Not verified by compilation, no Nix evaluator available in the environment that produced this. Before switching:
  nix flake lock
  nix flake check
  sudo nixos-rebuild dry-build --flake .#nixos
