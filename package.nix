{ lib, stdenvNoCC, python3, fzf, git, zsh }:
stdenvNoCC.mkDerivation {
  pname = "token-completion";
  version = "0.1.0";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    dir="$out/share/token-completion"
    install -Dm644 -t "$dir" token-completion.plugin.zsh dotenv.py providers.py
    substituteInPlace "$dir/token-completion.plugin.zsh" \
      --replace-fail '_tc_python=python3 _tc_fzf=fzf' '_tc_python=${python3}/bin/python3 _tc_fzf=${fzf}/bin/fzf'
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
