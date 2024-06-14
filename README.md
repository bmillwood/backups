# backup scripts

These scripts are what I use to manage my backups from my laptop to my external
disks.

I take a snapshot of my btrfs root disk at boot, every boot, and put it in
`${snap_root}/"$(date --utc --rfc-3339=seconds)"`. In retrospect I might have
preferred `date --utc --iso-8601=seconds` to avoid the space. The important
thing is that the snapshots are named so that time-ordering and name-ordering
coincide, and they start with YYYY-MM.

I use `btrfs_sends.py` to send these snapshots to btrfs-formatted USB disk
drives that I plug in once a month. Since the disks keep all snapshots forever,
they're subdirectoried by year, which I don't bother with locally.

I use `zfs_rsync.py` to insure against a data-losing btrfs bug by also copying
the first snapshot in each month to a zfs disk and then snapshotting that. (This
is where we use the assumption that snapshot names begin with YYYY-MM, because
those are the zfs snapshot names).

Both use `config.py` which needs to define three values of type `set[str]`:
- `btrfs_remotes`, which is the possible places where the btrfs send targets
  could be. The script proceeds if and only if exactly one of them is present.
- `zfs_pools`, which names the zfs pools the script is willing to use as
  targets. It's OK to have multiple present at once.
- `btrfs_srcs`, which is where all the local snapshots exist. It's a set and not
  just a single path so that you can pass the partitioned-by-year directories
  from a send target, in case you want to send *from* one of these disks as
  well.

`config.py` is not included because I personally find it embarrassing for people
to know the names of my disks and pools.

## Known problems

### Performance

#### No rename detection

The rsync doesn't detect renames, they become an add + delete. This is a quite
common complaint about rsync, so there are some patches to address it, but I
haven't tried to use them, and would prefer to stick to "vanilla" rsync code,
since correctness is important here.

I will probably address this by inferring renames from the btrfs sources,
perhaps using `btrfs receive --dump`, and apply those renames before doing the
rsync. In principle, this shouldn't impact correctness, since the rsync would
correct any mistakes I made.

(It's also possible for this to result in larger snapshots, e.g. if what you
did on the source was `mv A B; cp B A; completely rewrite B` and you'd actually
rather rsync match up old-A with new-A than new-B. But this seems unlikely in
practice.)

One disadvantage of doing it this way is that you need the btrfs "source"
snapshot to do it, not just the new snapshot that you are rsyncing. In the
absence of that you could still detect renames using entirely "content-based"
methods (e.g. using a content-addressable store, or even do something more
exotic that can recognise similar but not identical files), but it sounds like
more work than I'd like to do.

#### Unable to use no-whole-file + inplace rsync

Common wisdom to minimise changes with rsync in copy-on-write scenarios is to
use `--no-whole-file --inplace`, so that if a small part of your file is
updated, rsync won't rewrite the rest of the file. (Whole-file transfers are
default when both paths are local, as is the case here.) However, there's a
correctness problem with doing this in the presence of `--hard-links`, which is
that rsync won't break hard links in the destination that aren't present in the
source. Thus, if you have two hardlinked files in the source, you rsync them,
and then you break the hard links in the source, update one of them, and rsync
again, they may both be updated in the destination. Using whole-file updates
breaks the hard link and thus avoids the problem.

Another solution would be to drop `--hard-links`, but:

- faithful reproduction of as much as my filesystem as possible seems more
  important than using as little space as possible,
- my system does use hard links in some contexts, and I imagine most file
  modifications replace the whole file anyway, so it's not even clear which
  of the options minimizes space usage.
