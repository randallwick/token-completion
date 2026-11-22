{ lib, stdenvNoCC, python3, fzf, git, zsh }:
let
  interpreter = python3.withPackages (ps: [ ps.pyyaml ] ++ lib.optional (lib.versionOlder python3.version "3.11") ps.tomli);
in
stdenvNoCC.mkDerivation {
  pname = "token-completion";
  version = "0.2.0";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    dir="$out/share/token-completion"
    install -Dm644 -t "$dir" token-completion.plugin.zsh dotenv.py file_source.py providers.py
    substituteInPlace "$dir/token-completion.plugin.zsh" \
      --replace-fail '_tc_python=python3 _tc_fzf=fzf' '_tc_python=${interpreter}/bin/python3 _tc_fzf=${fzf}/bin/fzf'
    substituteInPlace "$dir/providers.py" \
      --replace-fail 'GIT = "git"' 'GIT = "${git}/bin/git"' \
      --replace-fail 'ZSH = "zsh"' 'ZSH = "${zsh}/bin/zsh"'
    runHook postInstall
  '';
  meta = {
    description = "Source-aware token completion for interactive zsh";
    platforms = lib.platforms.unix;
  };
}
