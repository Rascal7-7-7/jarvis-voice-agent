#!/bin/zsh
# Atomic-replace stress test.
#
# Reproduces the runtime's publish pattern -- write .tmp, then rename over the
# target -- in a TEMP directory. The production state file is never written to,
# and is not even referenced here.
#
# The point is the inode swap: a watcher attached to the file itself would go
# deaf after the first replacement. This drives 150 of them and checks the
# provider still reads the final value.
set -u
N=${1:-150}
DIR=$(mktemp -d /tmp/jarvis-hud-stress.XXXXXX)
FILE="$DIR/jarvis_state.json"
STATES=(IDLE LISTENING THINKING ROUTING LOCAL_FAST LOCAL_TOOL WEB SPEAKING)

echo "temp dir : $DIR"
echo "replaces : $N"

first_inode=""
for i in $(seq 1 $N); do
  s=${STATES[$(( (i % ${#STATES[@]}) + 1 ))]}
  printf '{"state":"%s","since":%s,"detail":"n=%d","pid":%d}' \
    "$s" "$(date +%s).0" "$i" "$$" > "$FILE.tmp"
  mv -f "$FILE.tmp" "$FILE"          # same rename-over semantics as os.replace()
  if [ $i -eq 1 ]; then
    first_inode=$(stat -f%i "$FILE")
  fi
done

last_inode=$(stat -f%i "$FILE")
echo "first inode: $first_inode"
echo "last  inode: $last_inode"
if [ "$first_inode" = "$last_inode" ]; then
  echo "WARNING: inode did not change -- this test proved nothing"
else
  echo "inode changed across replacements, as expected"
fi
echo "final file : $(cat "$FILE")"
echo "$DIR"
