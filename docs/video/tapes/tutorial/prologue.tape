# Off camera: the fresh checkout's CLI first in PATH, a short prompt, ~/inatrace-platform, a clean screen; then
# asciinema records the session (make.py --record sets $CAST; the video replays that).
Hide
Type `export PATH=~/inatrace-platform/bin:$PATH PS1='\[\e[38;5;244m\]\w\[\e[0m\] \[\e[1;32m\]❯\[\e[0m\] '; cd ~/inatrace-platform; clear; asciinema rec -q --overwrite -c 'bash --norc --noprofile' "$CAST"` Enter
Sleep 1s
Show
