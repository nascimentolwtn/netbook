#!/bin/bash

photo_root="$HOME/Imagens"
delay=15
screen_res="1024x600"
max_depth=15
hist_cap=30
queue_cap=10
queue_slots=24
weights_cache="$HOME/.cache/digitalframe_weights.tsv"
weights_lock="$HOME/.cache/digitalframe_weights.lock"

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
EOF

cleanup() {
   kill "$producer_pid" "$feh_pid" "$old_feh_pid" 2>/dev/null
   rm -rf "$shm_dir"
   if [ "$keys_existed" -eq 1 ]; then
      mv "$keys_backup" "$keys_file"
   else
      rm -f "$keys_file"
   fi
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
      "$1" < /dev/null > /dev/null 2>&1 &
   echo $!
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
# folders: a folder with 3 photos gets the same odds at the root as one
# with 4000+, so a specific photo in the small one could be ~1000x more
# likely to come up on any given pick. To fix that at the root (where the
# size gap between symlinked folders is most extreme) without paying for a
# full recursive count synchronously, per-top-level-folder file counts are
# computed once in the background (see compute_weights_bg) and cached to
# disk; root-level picks are weighted by those counts once available, and
# fall back to uniform (occasionally repeating small folders more often)
# until the first background count finishes.
declare -A root_weights
root_weights_mtime=0

reload_weights_if_changed() {
   [ -f "$weights_cache" ] || return
   local m
   m=$(stat -c %Y "$weights_cache" 2>/dev/null) || return
   [ "$m" = "$root_weights_mtime" ] && return
   root_weights=()
   while IFS=$'\t' read -r name count; do
      [ -n "$name" ] && root_weights["$name"]="$count"
   done < "$weights_cache"
   root_weights_mtime="$m"
}

compute_weights_bg() {
   ( mkdir -p "$HOME/.cache"
     # a lock older than 10 minutes (computation normally takes ~80s) means
     # whatever created it died without cleaning up -- e.g. the netbook lost
     # power mid-computation -- so treat it as stale rather than block
     # recomputation forever
     if [ -d "$weights_lock" ]; then
        lock_age=$(( $(date +%s) - $(stat -c %Y "$weights_lock" 2>/dev/null || echo 0) ))
        [ "$lock_age" -gt 600 ] && rmdir "$weights_lock" 2>/dev/null
     fi
     if ! mkdir "$weights_lock" 2>/dev/null; then
        # another instance is already computing; leave it alone rather
        # than duplicate ~80s of work
        exit 0
     fi
     trap 'rmdir "$weights_lock" 2>/dev/null' EXIT
     local tmp="$weights_cache.tmp.$$"
     : > "$tmp"
     local d name n
     for d in "$photo_root"/*/; do
        name=$(basename "$d")
        n=$(find -L "$d" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' -o -iname '*.bmp' \) 2>/dev/null | wc -l)
        printf '%s\t%s\n' "$name" "$n" >> "$tmp"
     done
     mv "$tmp" "$weights_cache"
   ) &
}

# Weighted pick among root-level entries using root_weights (falls back to
# weight 1 for anything not yet counted, e.g. a folder just symlinked in).
# Combines two $RANDOM draws since total weight (~69000+) exceeds $RANDOM's
# own 0-32767 range.
weighted_root_pick() {
   local -a cum
   local total=0 i w name
   for i in "${!entries[@]}"; do
      name=$(basename "${entries[$i]}")
      w="${root_weights[$name]:-1}"
      [ "$w" -le 0 ] 2>/dev/null && w=1
      total=$(( total + w ))
      cum[$i]=$total
   done
   local r=$(( (RANDOM * 32768 + RANDOM) % total ))
   for i in "${!entries[@]}"; do
      if [ "$r" -lt "${cum[$i]}" ]; then
         echo "${entries[$i]}"
         return 0
      fi
   done
   echo "${entries[-1]}"
}

pick_random_photo() {
   local dir="$photo_root"
   local depth=0 tries=0 entries pick
   while [ "$tries" -lt 40 ]; do
      tries=$((tries + 1))
      entries=()
      while IFS= read -r -d '' entry; do
         entries+=("$entry")
      done < <(find -L "$dir" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
      if [ "${#entries[@]}" -eq 0 ]; then
         dir="$photo_root"
         depth=0
         continue
      fi
      if [ "$dir" = "$photo_root" ]; then
         reload_weights_if_changed
         pick=$(weighted_root_pick)
      else
         pick="${entries[RANDOM % ${#entries[@]}]}"
      fi
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

# Recompute folder weights in the background if missing or stale (a folder
# was added/removed from $photo_root since the cache was built). Picks
# before this finishes just fall back to uniform (see weighted_root_pick),
# so this never blocks the first photo or the initial queue fill.
if [ ! -f "$weights_cache" ] || [ "$photo_root" -nt "$weights_cache" ]; then
   compute_weights_bg
fi

first_photo=$(pick_random_photo) || { echo "no photos found under $photo_root" >&2; exit 1; }
record_new_photo "$first_photo"
resolve_photo "$first_photo" "${slot_tmp[0]}" "${slot_out[0]}"
show_file=$(cat "${slot_out[0]}")
cur_photo="$first_photo"
cur_slot=0

rm -f "$direction_file"
feh_pid=$(launch_feh "$show_file")
start_time=$SECONDS

# Only start building the lookahead queue once the first photo is already
# up, so it doesn't compete for CPU with the very first, unavoidably
# synchronous pick+resolve above.
run_producer &
producer_pid=$!

while true; do
   direction=""
   while true; do
      if [ -f "$direction_file" ]; then
         direction=$(cat "$direction_file")
         break
      fi
      if ! kill -0 "$feh_pid" 2>/dev/null; then
         # feh exited on its own (q/Escape) -- fall through to desktop
         break 2
      fi
      if [ $(( SECONDS - start_time )) -ge "$delay" ]; then
         direction="next"
         break
      fi
      sleep 0.2
   done

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
   feh_pid=$(launch_feh "$show_file")
   sleep 0.6
   kill "$old_feh_pid" 2>/dev/null
   start_time=$SECONDS
done
