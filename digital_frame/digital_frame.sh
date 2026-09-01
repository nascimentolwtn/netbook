#!/bin/bash

photo_root="$HOME/Imagens"
delay=15
screen_res="1024x600"
max_depth=15
hist_cap=30
queue_cap=15
queue_slots=36
weights_cache="$HOME/.cache/digitalframe_weights.tsv"
weights_lock="$HOME/.cache/digitalframe_weights.lock"
listing_cache_dir="$HOME/.cache/digitalframe_listing"

# Crash log file for diagnostics
crash_log="$HOME/.cache/digitalframe_crashes.log"
mkdir -p "$(dirname "$crash_log")"

log_crash() {
   local msg="$1"
   echo "[$(date '+%Y-%m-%d %H:%M:%S')] $msg" >> "$crash_log"
}

# /dev/shm is tmpfs (RAM-backed) on this machine -- scratch files live there
# instead of /tmp (a real ext4 partition) so the per-photo downscale/resolve
# work never touches disk. Even with the lookahead queue below, resident
# size stays small (queue_slots * ~200-350KB), negligible on a 2GB machine.
shm_dir="/dev/shm/digitalframe_$$"
mkdir -p "$shm_dir"
direction_file="$shm_dir/direction"
queue_dir="$shm_dir/queue"
mkdir -p "$queue_dir"
keys_file="$HOME/.config/feh/keys"
keys_backup="${keys_file}.bak_digitalframe"

# Two scaled-photo/resolved-path slots, ping-ponged, used only for Left
# (prev) and for stepping forward through already-visited history -- both
# rare, manual actions, so a small synchronous resolve there is fine.
slot_tmp=("$shm_dir/scaled_0.jpg" "$shm_dir/scaled_1.jpg")
slot_out=("$shm_dir/resolved_0" "$shm_dir/resolved_1")
pause_tmp="$shm_dir/paused_overlay.jpg"

# This runs per-photo, so Left/Right need a custom action instead of feh's
# native next_img/prev_img (useless with only one image loaded at a time).
# remove/delete are feh's own built-in functions hardcoded to Delete/
# Ctrl+Delete (independent of the action_N mechanism) -- left bound, Delete
# would ALSO run feh's built-in "remove current file from filelist" on top
# of our action_3, which with only one file loaded empties the filelist and
# makes feh quit immediately (which then tears down the whole script, since
# the main loop treats feh exiting on its own as "user pressed q"). Explicit
# blank bindings here unbind them, same as prev_img/next_img below.
# feh keybindings are global, so save/restore whatever was there before.
mkdir -p "$HOME/.config/feh"
keys_existed=0
if [ -f "$keys_file" ]; then
   keys_existed=1
   cp "$keys_file" "$keys_backup"
fi
cat > "$keys_file" <<'EOF'
prev_img
next_img
remove
delete
action_1 Left
action_2 Right
action_3 Delete
action_4 space
EOF

cleanup() {
   kill "$producer_pid" "$feh_pid" "$old_feh_pid" "$watchdog_pid" 2>/dev/null
   rm -rf "$shm_dir"
   if [ "$keys_existed" -eq 1 ]; then
      mv "$keys_backup" "$keys_file"
   else
      rm -f "$keys_file"
   fi
   log_crash "SHUTDOWN: normal exit"
}
trap cleanup EXIT

# Downscales every photo to screen size (feh loading a full ~9MP photo fresh
# takes ~2s on this CPU) and corrects real EXIF-tagged rotation so photos are
# upright. Deliberately does NOT force-rotate portrait photos 90 degrees to
# fill the landscape screen -- that makes them sideways, which is worse than
# just letterboxing them upright with black bars on the sides.
# Uses -sample instead of -resize: measured ~3-4x faster on this CPU (a
# cheap nearest-neighbor-ish downscale instead of a proper resampling
# filter), and the quality difference is imperceptible at this screen size.
# Also burns in an "immediate folder/filename.ext" label (top-right -- the
# netbook's display is damaged on the left side -- white on a dark
# translucent box) in the same convert call -- measured ~0.15-0.2s
# added on top of the decode+resize (which itself dominates at ~1-1.3s for a
# typical full-size photo), so it rides along cheaply rather than needing a
# second pass or relying on feh's own (basename-only) --draw-filename. %%
# escapes a literal "%" in case a real filename has one, since IM's
# -annotate text otherwise treats % as a property-expansion char.
label_for() {
   local file="$1" label
   label="$(basename "$(dirname "$file")")/$(basename "$file")"
   echo "${label//%/%%}"
}

resolve_photo() {
   local file="$1" tmp="$2" out="$3" label
   label=$(label_for "$file")
   if convert "$file" -auto-orient -sample "${screen_res}>" \
        -gravity NorthEast -pointsize 20 -fill white -undercolor '#00000099' \
        -annotate +10+10 "$label" "$tmp" 2>/dev/null; then
      echo "$tmp" > "$out"
   else
      echo "$file" > "$out"
   fi
}

# Runs feh in the background and prints its PID. The action just records
# which arrow was pressed -- the main loop below does the actual killing,
# once the replacement window is already up (see the overlap below).
launch_feh() {
   feh -Y -x -q -B black -F -Z \
      --action1 "echo prev > '$direction_file'" \
      --action2 "echo next > '$direction_file'" \
      --action3 "echo delete > '$direction_file'" \
      --action4 "echo pause > '$direction_file'" \
      "$1" < /dev/null > /dev/null 2>&1 &
   echo $!
}

# Applies the "PAUSED" indicator (bottom-right -- a different corner from
# the filename label in the top-right, so they don't overlap) on top of the
# already-resolved/downscaled $1 when paused=1, otherwise passes it through
# unchanged. Always reads from the canonical unpaused $show_file rather
# than a previous paused output, so repeated toggling never stacks the
# overlay on top of itself. Runs on an already screen_res-sized image, so
# it's cheap even though it re-runs on every manual advance while paused.
apply_pause_overlay() {
   local src="$1"
   if [ "$paused" -eq 0 ]; then
      echo "$src"
      return
   fi
   if convert "$src" -gravity SouthEast -pointsize 22 -fill yellow \
        -undercolor '#00000099' -annotate +10+10 "PAUSED  (space to resume)" \
        "$pause_tmp" 2>/dev/null; then
      echo "$pause_tmp"
   else
      echo "$src"
   fi
}

# Modal confirmation for the Delete key, shown over the fullscreen feh
# window. Displays the resolved real path (not the ~/Imagens symlink path)
# so the user sees exactly what's about to be permanently removed from
# /media/backup. Returns 0 for Yes, non-zero for No/closed/Escape.
confirm_delete() {
   local logical="$1" real
   real=$(readlink -f "$logical")
   [ -z "$real" ] && real="$logical"
   zenity --question --title="Delete photo?" \
      --text="Permanently delete this photo?\n\n$real" \
      --width=500 2>/dev/null
}

# Deletes the currently displayed photo. Top-level folders under
# $photo_root are themselves symlinks into /media/backup (see
# pick_random_photo's use of find -L), so the individual photo path is
# normally a real file reached through a symlinked ancestor directory --
# resolving with readlink -f and deleting *that* is what actually frees
# space on the drive. Also removes $logical itself if it happens to be a
# symlink (e.g. a per-file symlink), so nothing dangling is left in the
# library either way.
do_delete() {
   local logical="$1" real
   real=$(readlink -f "$logical")
   [ -n "$real" ] && rm -f "$real"
   [ -L "$logical" ] && rm -f "$logical"
}

# Random descent through the directory tree instead of enumerating every
# file up front -- the library is 69000+ files across a growing set of
# symlinked folders and only grows as more get added, so a full scan takes
# well over a minute. Each pick costs one shallow directory listing per
# level (fast, independent of total library size), not a full-tree walk.
#
# Uniform-per-entry selection at every level badly over-represents small
# folders: e.g. inside archive_fotos, "2017-01-04 UTG Jan2017" (13 photos)
# used to get the same 1/52 odds as "Album Casal" (29000+ photos), making
# individual photos in the small folder ~2000x more likely to come up than
# ones in the big folder -- and this compounds at every nesting level, not
# just the root. To fix that everywhere without paying for a full recursive
# count synchronously on each pick, recursive per-directory file counts
# (covering every directory in the tree, any depth) are computed once in
# the background (see compute_weights_bg) and cached to disk keyed by full
# path; picks at every level are weighted by those counts once available,
# and fall back to uniform (weight 1) for any entry not yet counted (e.g. a
# folder just symlinked in) until the first background count finishes.
#
# Every call site invokes pick_random_photo via `$(...)` to capture its
# echoed path, which forks a subshell -- a bash associative array loaded
# inside that subshell (or anything it calls) is gone the moment the call
# returns, so there is no point trying to cache the parsed weights table
# across picks in a shell variable; weighted_pick instead does one targeted
# awk pass per directory level, filtering weights_cache down to just the
# handful of entries at that level, which is both simpler and faster than
# parsing the whole (multi-thousand-line) file into bash on every call.
compute_weights_bg() {
   ( mkdir -p "$HOME/.cache"
     # a lock older than 10 minutes (computation normally takes a couple
     # minutes on this NTFS/FUSE-mounted backup drive) means whatever
     # created it died without cleaning up -- e.g. the netbook lost power
     # mid-computation -- so treat it as stale rather than block forever
     if [ -d "$weights_lock" ]; then
        lock_age=$(( $(date +%s) - $(stat -c %Y "$weights_lock" 2>/dev/null || echo 0) ))
        [ "$lock_age" -gt 600 ] && rmdir "$weights_lock" 2>/dev/null
     fi
     if ! mkdir "$weights_lock" 2>/dev/null; then
        # another instance is already computing; leave it alone rather
        # than duplicate the work
        exit 0
     fi
     trap 'rmdir "$weights_lock" 2>/dev/null' EXIT
     local tmp="$weights_cache.tmp.$$"
     local files_raw="$tmp.files"
     local direct="$tmp.direct"
     local dirs="$tmp.dirs"

     # The full-tree walk is the expensive part on this slow NTFS/FUSE
     # mount, so it's done exactly once here and reused for both the
     # per-directory counts below and the listing cache further down,
     # rather than walking the tree twice.
     find -L "$photo_root" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.bmp' \) 2>/dev/null > "$files_raw"

     # Direct (non-recursive) image counts, one line per directory that
     # directly contains images, keyed by the same $photo_root-prefixed
     # traversal path pick_random_photo itself builds (not a realpath --
     # symlinked folders like archive_fotos need to key consistently with
     # how entries[] is populated below).
     awk '{parent=$0; sub(/\/[^/]*$/, "", parent); print parent}' "$files_raw" \
        | sort | uniq -c | awk '{c=$1; $1=""; sub(/^ /,""); print c"\t"$0}' > "$direct"

     # Every directory in the tree, deepest first (most path separators
     # first) so a single pass can fold each directory's total into its
     # parent exactly once, after all of that directory's own children
     # (which sort earlier, being deeper) have already folded into it --
     # a post-order aggregation done via sort order instead of recursion.
     find -L "$photo_root" -type d 2>/dev/null \
        | awk -F/ '{print NF"\t"$0}' | sort -t $'\t' -k1,1nr | cut -f2- > "$dirs"

     awk -F'\t' '
        FNR==NR { direct[$2]=$1; next }
        {
           total[$0] += direct[$0] + 0
           print $0 "\t" total[$0]
           parent = $0
           sub(/\/[^/]*$/, "", parent)
           total[parent] += total[$0]
        }
     ' "$direct" "$dirs" > "$tmp"
     mv "$tmp" "$weights_cache"

     # Immediate-children listing (dir or file) for every directory in the
     # tree, so pick_random_photo can read children from this fast local
     # cache instead of running a live `find` against the slow mount at
     # every level of every single pick. This is INDEXED, not one flat
     # file: an earlier version stored every "parent\tchild" pair in one
     # ~12MB file, and a linear awk scan over that (needed once per
     # directory level per pick) cost ~2s each -- as slow as the live
     # `find` over FUSE it was meant to replace. Sorting by parent first
     # makes each directory's children arrive as one contiguous run, so a
     # single sequential pass can write an index.tsv (path -> numeric id,
     # small enough to scan in tens of ms) plus one small per-directory
     # children file, closing each children file before opening the next
     # -- at most 2 file handles open at once, however many directories
     # there are.
     rm -rf "$listing_cache_dir"
     mkdir -p "$listing_cache_dir"
     { cat "$dirs"; cat "$files_raw"; } \
        | awk '{parent=$0; sub(/\/[^/]*$/, "", parent); print parent "\t" $0}' \
        | LC_ALL=C sort -t $'\t' -k1,1 \
        | awk -F'\t' -v outdir="$listing_cache_dir" '
           {
              if ($1 != prev) {
                 if (out != "") close(out)
                 idx++
                 print $1 "\t" idx >> (outdir "/index.tsv")
                 out = outdir "/" idx
                 prev = $1
              }
              print $2 > out
           }
        '

     rm -f "$direct" "$dirs" "$files_raw"
   ) &
}

# Populates the caller's $entries with the immediate children of $1,
# preferring the cached index (fast, local disk, touches only a few KB)
# and falling back to a live `find` (slow, hits the FUSE-mounted backup
# drive) only when the cache is missing or doesn't yet cover this
# directory -- e.g. a folder just symlinked in since the last background
# scan.
list_children() {
   local dir="$1" child idx
   entries=()
   if [ -f "$listing_cache_dir/index.tsv" ]; then
      idx=$(awk -F'\t' -v want="$dir" '$1==want{print $2; exit}' "$listing_cache_dir/index.tsv")
      if [ -n "$idx" ] && [ -f "$listing_cache_dir/$idx" ]; then
         while IFS= read -r child; do
            entries+=("$child")
         done < "$listing_cache_dir/$idx"
         [ "${#entries[@]}" -gt 0 ] && return 0
      fi
   fi
   while IFS= read -r -d '' child; do
      entries+=("$child")
   done < <(find -L "$dir" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
}

# Weighted pick among $entries. Looks up weights for just this level's
# entries via one awk pass over weights_cache (falls back to weight 1 for
# anything not yet counted -- files themselves are never in weights_cache,
# only directories, so individual photos within the same folder stay
# uniform relative to each other, which is what we want). Reads
# /dev/urandom rather than $RANDOM: pick_random_photo runs in a fresh
# subshell on every call (see above), and bash subshells forked in quick
# succession without the parent ever touching $RANDOM in between can
# inherit identical PRNG state and hand back the very same "random" value
# call after call -- /dev/urandom has no such state to desync.
weighted_pick() {
   local -a cum
   local total=0 i w
   local -A w_lookup=()
   if [ -f "$weights_cache" ] && [ "${#entries[@]}" -gt 0 ]; then
      # entries is passed as a process-substitution "file" (FNR==NR side),
      # not a -v argument -- a leaf folder like Photos_LW has ~4000 files,
      # and joining that many paths into one -v string blew past Linux's
      # ~128KB single-argument limit (MAX_ARG_STRLEN), which made awk fail
      # outright with "argument list too long" on exactly the large flat
      # folders this cache was supposed to make fast.
      while IFS=$'\t' read -r p w; do
         w_lookup["$p"]="$w"
      done < <(awk -F'\t' '
         FNR==NR { want[$0]=1; next }
         ($1 in want) { print }
      ' <(printf '%s\n' "${entries[@]}") "$weights_cache")
   fi
   # A leaf folder (all files, no subdirectories) never matches anything
   # in weights_cache -- only directories are counted there -- so every
   # entry falls back to weight 1 regardless. Detecting that up front and
   # doing a single O(1) uniform pick skips the O(n) cumulative-sum
   # construction below, which for a folder with thousands of files (e.g.
   # Photos_LW's ~4000) was itself becoming the dominant per-pick cost
   # once the cache lookups were fixed. Directory-only levels (the ones
   # that actually need weighting) have far fewer entries, so paying the
   # O(n) cost there is negligible.
   if [ "${#w_lookup[@]}" -eq 0 ]; then
      local n=${#entries[@]}
      local ur=$(od -An -tu4 -N4 /dev/urandom 2>/dev/null | tr -d " " | awk "{print \$1 % $n}")
      echo "${entries[$ur]}"
      return 0
   fi
   for i in "${!entries[@]}"; do
      w="${w_lookup[${entries[$i]}]:-1}"
      [ "$w" -le 0 ] 2>/dev/null && w=1
      total=$(( total + w ))
      cum[$i]=$total
   done
   local r=$(od -An -tu4 -N4 /dev/urandom 2>/dev/null | tr -d " " | awk "{print \$1 % $total}")
   for i in "${!entries[@]}"; do
      if [ "$r" -lt "${cum[$i]}" ]; then
         echo "${entries[$i]}"
         return 0
      fi
   done
   echo "${entries[-1]}"
}

# Root-level folders split into two "realms" by where their symlink
# actually points -- recently synced camera rolls vs. the older bulk
# archive. archive_fotos alone is ~89% of total weight, so proportional
# weighting alone would make sync_data photos rare; picks strictly
# alternate realm at the root instead, then weighted_pick still applies
# proportionally *within* whichever realm was chosen.
sync_data_prefix="/media/backup/sync_data"
archive_prefix="/media/backup/archive"

# Every call site invokes pick_random_photo via `$(...)` to capture its
# echoed path, which forks a subshell -- any plain shell variable written
# inside pick_random_photo (or functions it calls) is gone the moment that
# call returns. The toggle has to survive across calls, so it lives in a
# file instead of a variable, same pattern as direction_file/weights_lock
# elsewhere in this script. Single-writer in practice (the one synchronous
# call before run_producer starts, then only run_producer itself), so a
# plain read-increment-write needs no locking.
root_group_toggle_file="$shm_dir/root_group_toggle"
echo 0 > "$root_group_toggle_file"

next_root_group() {
   local n
   n=$(cat "$root_group_toggle_file" 2>/dev/null || echo 0)
   echo $(( n + 1 )) > "$root_group_toggle_file"
   if [ $(( n % 2 )) -eq 1 ]; then
      echo "archive"
   else
      echo "sync"
   fi
}

classify_root_entry() {
   local real
   real=$(readlink -f "$1")
   case "$real" in
      "$sync_data_prefix"/*) echo "sync" ;;
      "$archive_prefix"/*) echo "archive" ;;
      *) echo "other" ;;
   esac
}

# Root only has ~6 entries, but classify_root_entry's readlink -f resolves
# through the slow FUSE mount, and pick_random_photo hits the root branch
# on every single call -- computed once here at startup instead of once
# per pick.
root_realm_cache="$shm_dir/root_realm.tsv"
: > "$root_realm_cache"
while IFS= read -r -d '' re; do
   printf '%s\t%s\n' "$re" "$(classify_root_entry "$re")" >> "$root_realm_cache"
done < <(find -L "$photo_root" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)

pick_random_photo() {
   local dir="$photo_root"
   local depth=0 tries=0 entries pick
   # Decided once per call (not per retry) -- a dead-end retry (e.g. an
   # empty leaf folder) re-enters the root branch below, and re-rolling
   # the realm on every such retry would desync the alternation seen
   # across consecutive delivered photos.
   local want
   want=$(next_root_group)
   while [ "$tries" -lt 40 ]; do
      tries=$((tries + 1))
      list_children "$dir"
      if [ "${#entries[@]}" -eq 0 ]; then
         dir="$photo_root"
         depth=0
         continue
      fi
      if [ "$dir" = "$photo_root" ]; then
         local -A root_realm=()
         local -a group_entries=() e
         local rp rr
         while IFS=$'\t' read -r rp rr; do
            root_realm["$rp"]="$rr"
         done < "$root_realm_cache"
         for e in "${entries[@]}"; do
            [ "${root_realm[$e]:-other}" = "$want" ] && group_entries+=("$e")
         done
         [ "${#group_entries[@]}" -gt 0 ] && entries=("${group_entries[@]}")
      fi
      pick=$(weighted_pick)
      if [ -d "$pick" ] && [ "$depth" -lt "$max_depth" ]; then
         dir="$pick"
         depth=$((depth + 1))
         continue
      elif [ -f "$pick" ]; then
         case "$pick" in
            *.[Pp][Nn][Gg]|*.[Jj][Pp][Gg]|*.[Jj][Pp][Ee][Gg]|*.[Gg][Ii][Ff]|*.[Bb][Mm][Pp])
               echo "$pick"
               return 0
               ;;
         esac
      fi
      dir="$photo_root"
      depth=0
   done
   return 1
}

# Runs for the whole session, keeping up to queue_cap upcoming photos
# picked+resolved ahead of time in $queue_dir, so mashing Right repeatedly
# pulls already-ready photos instead of waiting on convert each time.
# queue_slots > queue_cap gives headroom so a slot is never reused while a
# still-displayed photo might still reference it.
run_producer() {
   local n=0 count photo tmp marker label
   log_crash "PRODUCER: started (PID $$)"
   while true; do
      count=$(find "$queue_dir" -maxdepth 1 -name 'ready_*' 2>/dev/null | wc -l)
      if [ "$count" -ge "$queue_cap" ]; then
         sleep 0.3
         continue
      fi
      photo=$(pick_random_photo) || { sleep 1; continue; }
      tmp="$queue_dir/slot_$(( n % queue_slots )).jpg"
      marker=$(printf 'ready_%08d' "$n")
      label=$(label_for "$photo")
      {
         echo "$photo"
         if convert "$photo" -auto-orient -sample "${screen_res}>" \
              -gravity NorthEast -pointsize 20 -fill white -undercolor '#00000099' \
              -annotate +10+10 "$label" "$tmp" 2>/dev/null; then
            echo "$tmp"
         else
            echo "$photo"
         fi
      } > "$queue_dir/.tmp_$marker"
      mv "$queue_dir/.tmp_$marker" "$queue_dir/$marker"
      n=$((n + 1))
   done
}

# Watchdog: monitors run_producer and feh, logs when they die
run_watchdog() {
   log_crash "WATCHDOG: started (PID $$)"
   while true; do
      sleep 2
      if ! kill -0 "$producer_pid" 2>/dev/null; then
         log_crash "WATCHDOG: producer died (PID $producer_pid)"
         break
      fi
      if ! kill -0 "$feh_pid" 2>/dev/null; then
         log_crash "WATCHDOG: feh died (PID $feh_pid)"
      fi
   done
}

# Pops the oldest ready queue entry. Prints "orig_path" then "resolved_path"
# on two lines; returns 1 if the queue is currently empty (advancing faster
# than the producer can keep up -- rare, since it targets queue_cap ready ahead).
take_from_queue() {
   local f
   f=$(find "$queue_dir" -maxdepth 1 -name 'ready_*' 2>/dev/null | sort | head -1)
   [ -z "$f" ] && return 1
   cat "$f"
   rm -f "$f"
}

# Bounded history (not a full pre-built list) so Left/Right can step back
# and forward through recently-seen photos; brand new photos are only
# picked once past the front of it.
history=()
hist_pos=-1

record_new_photo() {
   history+=("$1")
   if [ "${#history[@]}" -gt "$hist_cap" ]; then
      history=("${history[@]:1}")
   fi
   hist_pos=$(( ${#history[@]} - 1 ))
}

# Recompute folder weights and the listing cache in the background if
# either is missing or stale (a folder was added/removed from $photo_root
# since the cache was built). Picks before this finishes just fall back to
# uniform weighting and live `find` (see weighted_pick / list_children),
# so this never blocks the first photo or the initial queue fill.
if [ ! -f "$weights_cache" ] || [ ! -f "$listing_cache_dir/index.tsv" ] || [ "$photo_root" -nt "$weights_cache" ]; then
   compute_weights_bg
fi

first_photo=$(pick_random_photo) || { echo "no photos found under $photo_root" >&2; log_crash "STARTUP: no photos found"; exit 1; }
record_new_photo "$first_photo"
resolve_photo "$first_photo" "${slot_tmp[0]}" "${slot_out[0]}"
show_file=$(cat "${slot_out[0]}")
cur_photo="$first_photo"
cur_slot=0
paused=0

rm -f "$direction_file"
feh_pid=$(launch_feh "$show_file")
log_crash "STARTUP: main started, initial feh PID $feh_pid, photo: $first_photo"
start_time=$SECONDS

# Only start building the lookahead queue once the first photo is already
# up, so it doesn't compete for CPU with the very first, unavoidably
# synchronous pick+resolve above.
run_producer &
producer_pid=$!
log_crash "STARTUP: producer started (PID $producer_pid)"

run_watchdog &
watchdog_pid=$!

while true; do
   direction=""
   while true; do
      if [ -f "$direction_file" ]; then
         direction=$(cat "$direction_file")
         break
      fi
      if ! kill -0 "$feh_pid" 2>/dev/null; then
         log_crash "MAIN: feh exited unexpectedly (PID $feh_pid)"
         # feh exited on its own (q/Escape) -- fall through to desktop
         break 2
      fi
      if [ "$paused" -eq 0 ] && [ $(( SECONDS - start_time )) -ge "$delay" ]; then
         direction="next"
         break
      fi
      sleep 0.2
   done

   if [ "$direction" = "pause" ]; then
      paused=$(( 1 - paused ))
      rm -f "$direction_file"
      old_feh_pid=$feh_pid
      feh_pid=$(launch_feh "$(apply_pause_overlay "$show_file")")
      sleep 0.6
      kill "$old_feh_pid" 2>/dev/null
      start_time=$SECONDS
      continue
   fi

   # zenity blocks here while feh keeps showing the current photo
   # underneath, so a "No"/Escape just resumes the slideshow untouched.
   if [ "$direction" = "delete" ]; then
      if confirm_delete "$cur_photo"; then
         do_delete "$cur_photo"
         new_history=()
         for h in "${history[@]}"; do
            [ "$h" = "$cur_photo" ] || new_history+=("$h")
         done
         history=("${new_history[@]}")
         hist_pos=$(( ${#history[@]} - 1 ))
         direction="next"
      else
         rm -f "$direction_file"
         start_time=$SECONDS
         continue
      fi
   fi

   other_slot=$(( 1 - cur_slot ))
   at_frontier=0
   [ "$hist_pos" -eq $(( ${#history[@]} - 1 )) ] && at_frontier=1

   if [ "$direction" = "prev" ]; then
      if [ "$hist_pos" -gt 0 ]; then
         hist_pos=$(( hist_pos - 1 ))
         resolve_photo "${history[$hist_pos]}" "${slot_tmp[$other_slot]}" "${slot_out[$other_slot]}"
         show_file=$(cat "${slot_out[$other_slot]}")
         cur_photo="${history[$hist_pos]}"
         cur_slot=$other_slot
      else
         # nothing further back -- just re-show the current photo
         :
      fi
   else
      if [ "$at_frontier" -eq 1 ]; then
         if qline=$(take_from_queue); then
            orig_path=$(sed -n '1p' <<< "$qline")
            resolved_path=$(sed -n '2p' <<< "$qline")
         else
            log_crash "MAIN: queue starved, fallback sync resolve"
            # queue ran dry (advancing faster than it can refill) -- fall
            # back to a synchronous pick+resolve, same cost as before
            orig_path=$(pick_random_photo)
            resolve_photo "$orig_path" "${slot_tmp[$other_slot]}" "${slot_out[$other_slot]}"
            resolved_path=$(cat "${slot_out[$other_slot]}")
            cur_slot=$other_slot
         fi
         record_new_photo "$orig_path"
         show_file="$resolved_path"
         cur_photo="$orig_path"
      else
         # stepping forward through already-visited history
         hist_pos=$(( hist_pos + 1 ))
         resolve_photo "${history[$hist_pos]}" "${slot_tmp[$other_slot]}" "${slot_out[$other_slot]}"
         show_file=$(cat "${slot_out[$other_slot]}")
         cur_photo="${history[$hist_pos]}"
         cur_slot=$other_slot
      fi
   fi

   rm -f "$direction_file"
   old_feh_pid=$feh_pid
   feh_pid=$(launch_feh "$(apply_pause_overlay "$show_file")")
   sleep 0.6
   kill "$old_feh_pid" 2>/dev/null
   start_time=$SECONDS
done
