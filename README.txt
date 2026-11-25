CS2 SQUAD TRACKER
=================

Tracks how you and your squad play in CS2, from your own demo files.


HOW TO START
------------
1. Unzip this folder anywhere you like (Desktop, Documents, a USB stick...).
2. Double-click Start.bat.
3. Your browser opens with the tracker. If it does not, look at the black
   window: it shows the address to open.

The very first start downloads Python (about 25 MB), so you need internet
that one time. It takes a minute or two. After that it starts in seconds.


FIRST START
-----------
The tracker walks you through three short steps:
1. Your Steam ID. Paste your Steam profile link or the 17-digit number.
   To find it: in Steam, open your profile and copy the page address.
   A custom link (steamcommunity.com/id/yourname) works once you have
   added your API key in step 2; until then, paste the number.
2. Match history access. Settings opens with a note: add your Steam Web
   API key, your authentication code and your most recent match's
   sharing code, then press Test. See "MATCH HISTORY ACCESS" below.
   You can skip this and do it later.
3. Older matches (optional). Import your Steam match history so your
   stats cover more than the matches you play from now on. See "STEAM
   MATCH HISTORY" below.
After that, the more you play with the tracker running, the more it
knows: every new match is fetched and read automatically.


WHAT'S NEW IN V1.3.2
--------------------
- Fixed: importing a saved Steam match history page always failed with
  "That file isn't a Steam match history export or page." It works now.
- A page saved without its matches (with "HTML only") now tells you how
  to save it properly. See "STEAM MATCH HISTORY" below.


WHAT'S NEW IN V1.3.1
--------------------
- Ready to share: the download no longer contains anyone's data.
- A welcome screen on first start, with the setup steps.
- Your Steam ID can be your profile link.
- Upgrading from v1.3 needs no setup: the tracker keeps your Steam ID
  (or works it out from your matches and tells you once).


WHAT'S NEW IN V1.3
------------------
- Steam match history import (Competitive and Premier): adds your whole
  history. See "STEAM MATCH HISTORY" below.
- Awards of the night and Roast of the night on the Summary. You choose
  the roast level on the Summary page: trash talk, friendly or off.
- A bigger scoreboard: HS%, KAST, opening duels, clutches, rating and role.
- Records and milestones.
- A Rivals tab: the players you keep meeting, with you or against you.


WHAT'S NEW IN V1.2
------------------
- Every match is fetched through your Steam match-history access, so none
  are missed.
- Matches update automatically.
- Event files keep all the stats, so old demo files can be deleted.
- A setting to delete demos after they are read.
- Only one copy of the tracker can run at a time.


UPDATING FROM AN OLDER VERSION
------------------------------
1. Close the old version first: its black window and its browser tab.
2. Unzip the new version into a new folder and double-click Start.bat
   there. The first start of the new folder needs internet once: it
   downloads Python again, just like the first install did.
3. Your data in %APPDATA%\CS2SquadTracker is kept and upgraded
   automatically (a backup is made first).
4. Press "Re-read all demos" once, so the new stats are worked out.
5. Afterwards you can delete the old folder.


STEAM MATCH HISTORY
-------------------
You can add your whole match history from Steam, not only recent matches.
1. In Steam open your profile, then Games, then Counter-Strike 2, then
   Personal Game Data.
2. Open "Ranked Competitive Matches". Press "Load more history" until it
   stops. Then save the page: Ctrl+S, and choose "Webpage, Complete".
   (Not "HTML only": that saves the page as it was before the matches
   loaded, so the file has no matches in it.)
3. Do the same for "Premier Matches".
4. In the tracker open Settings, then "Import Steam match history", and
   choose each saved file.
Importing again later only adds the new matches.
Only you can see that Steam page. The saved file stays on your PC.


MATCH HISTORY ACCESS
--------------------
The tracker needs three things from Steam, set up once:
  1. Get a Steam Web API key at https://steamcommunity.com/dev/apikey
     (any domain name will do, e.g. localhost).
  2. Create your game authentication code under "Access to Your Match
     History" at
     https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128
  3. A match sharing code. New to the tracker? Copy the code of your
     most recent match, shown on the same Steam page as the
     authentication code. If the tracker already has your recent
     matches, leave it empty, or paste an older code to go further
     back (in CS2: Watch > Your Matches, copy the sharing code of that
     match). The tracker then imports that match and every one after it.
Then open Settings > Match history access in the tracker, paste them in
and press Test.
The key and the code are stored only on this PC.


"WINDOWS PROTECTED YOUR PC" (SMARTSCREEN)
-----------------------------------------
Windows shows this for any program that is not from a big company. The
tracker is safe to run; it only works with your CS2 demo files and your
Steam match list. To get past it:
  1. Click "More info" in the blue window.
  2. Click "Run anyway".
You only need to do this once.


WHILE YOU USE IT
----------------
Keep the black window open while you use the tracker. Closing the black
window quits the app.


UPDATE BUTTON
-------------
The Update button fetches your new matches' demos and reads them.
  - Steam must be running, and you logged in, to download demos.
  - Demos aren't fetched while CS2 is running: they wait, and are
    fetched on the next Update after you close CS2.
  - With match history access set up (see above), the tracker finds
    every match you play, not only the last 8.
  - Updates also run automatically: when you open the tracker (if the
    last one was over 30 minutes ago) and every 2 hours while it is
    open. Turn off "Update automatically" in Settings to stop this.


YOUR DATA
---------
Everything is saved on your PC in:
    %APPDATA%\CS2SquadTracker
(paste that into the Explorer address bar to open it). Backups are made
automatically, so you will not lose your stats if something goes wrong.
To move to a new PC, copy that folder across.


RESTORING A BACKUP
------------------
1. Close the app (close the black window).
2. Open %APPDATA%\CS2SquadTracker\backups\ and pick a backup file
   (the name has the date and time it was made, e.g.
   cs2tracker-20261123T201500.123456.db).
3. Copy it into %APPDATA%\CS2SquadTracker\, over tracker.db
   (rename the copy to tracker.db and replace the old file).
4. Double-click Start.bat again.


CREDITS
-------
- demoparser2 (reads CS2 demo files)
- boiler-writter (downloads demos from Steam). See the "licenses" folder.
- Barlow and Barlow Condensed fonts, under the SIL Open Font License.
  See the "licenses" folder.
