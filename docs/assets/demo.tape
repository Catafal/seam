# Seam demo GIF — regenerate with:  vhs docs/assets/demo.tape
# (requires `vhs`: brew install vhs / https://github.com/charmbracelet/vhs)
# Run from the repo root; uses the installed `seam` CLI on PATH.

Output docs/assets/seam-demo.gif

Set Shell zsh
Set FontSize 18
Set Width 1200
Set Height 760
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 55ms
Set PlaybackSpeed 1.0

# Setup (not recorded): allow `#` comments in the demo shell + start clean.
Hide
Type "setopt interactive_comments"
Enter
Type "clear"
Enter
Show

# --- the question every agent re-answers from scratch ---
Type "# index a codebase once..."
Sleep 700ms
Enter
Type "seam init ."
Sleep 400ms
Enter
Sleep 5s

Type "clear"
Enter
Sleep 300ms

# --- the money shot: 'what breaks if I change init_db?' answered instantly, by risk tier ---
Type "# ...then ask: what breaks if I change init_db?"
Sleep 700ms
Enter
Type "seam impact init_db"
Sleep 400ms
Enter
Sleep 4s

# --- a second capability: find code by concept, ranked ---
Type "clear"
Enter
Sleep 300ms
Type "# find code by meaning, not just string match"
Sleep 700ms
Enter
Type "seam search 'fts5 search'"
Sleep 400ms
Enter
Sleep 4s
