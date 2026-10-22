# Installing, updating and removing

## What it needs

- **Windows, 64-bit.** The installer, the uninstaller, *Show in folder* and the start-up messages all use Windows features. There is no Mac or Linux version of the installed program.
- **No administrator rights.** It installs for you alone, into your own AppData folder.
- **WebView2**, the browser engine built into Windows. The app opens in a window of its own, drawn by WebView2, which comes with Windows 11 and is on nearly every Windows 10 computer. Without it the app opens in your default browser instead, at `http://127.0.0.1:8000` (a current Edge or Chrome is best; the *Share* button needs one of them).
- **About 270 MB** for the program, plus whatever your data grows to. The data folder keeps a full copy of every file you give the app.
- **No internet connection** to work. The text-recognition models ship inside the program. When the computer is online the app looks for updates (below) - the only thing it uses the internet for.

## Installing

`Install.exe` is the only file you need (about 139 MB; a zipped copy is provided because email often blocks .exe attachments). Double-click it. A console window shows what it is doing:

- It installs to `%LOCALAPPDATA%\Programs\Assurance Console` - on most machines `C:\Users\<you>\AppData\Local\Programs\Assurance Console`.
- On a first install it asks **where your data should be kept** (see *Where your data is kept* below). Press Enter to keep it with the program, or type C to choose a folder in Windows' own folder picker.
- It shows a progress bar while it copies the files.
- It makes three shortcuts - **Assurance Console** on the desktop and in the Start menu, and **Uninstall Assurance Console** in the Start menu - and adds the app to *Settings > Apps* (Add/Remove Programs).
- It ends by showing where it installed, where your data is, and how to remove it, then asks *Start it now? [Y/n]*.

If saved work from an earlier install is found - you uninstalled but kept your data - it says so and picks it up again, without asking about the folder.

x There is no rollback on a first install that fails part-way (a full disk, for example); run it again once there is space.

## Updating

**By itself, when you are online.** A little after the app starts, and every six hours while it stays open, it asks its release page on GitHub whether there is a newer version. When there is, it downloads it in the background, checks it (see *Privacy*), and shows **Update ready** at the top of the window. Most updates are the app's own code alone, a few MB; only one that brings a new Python or new libraries is the whole program again. *Settings > The app* then says *"Version 2.3.5 is ready to install (the app's own code, 2.4 MB)"*, with what is new; press **Restart and update**. The app closes, the installer runs in a console window of its own - the same steps as below - and the app opens again on the new version. Nothing is installed until you press that button.

*Settings > The app* also shows the version you have, when it last looked, and **Check now**. Untick **Check for updates when this computer is online** to stop the checks altogether.

**By hand.** Run a newer `Install.exe`. It sees the copy already installed and updates it in place, with no questions:

- It says when the old copy was installed and **where your data is**, and that your saved runs and export registers stay exactly as they are.
- If the app is open, it closes it (only the copy in the install folder - a copy started from anywhere else is left alone), and opens it again when it finishes.
- The old program is moved aside, the new one copied in, and the old one deleted. If anything stops the copy part-way (a file held open by an antivirus scan, a full disk), the new files are removed and **the old version is put back**, so it still works.
- Your data folder and the note of where it is are never touched.

+ Updating keeps your data, your settings, your chosen data folder, and every kept file.
+ An update the app fetches must carry the publisher's signature and match its fingerprint, or it is thrown away - and it is always newer than the version you have.
x Versions before 2.2.0 cannot update themselves: install 2.2.0 by hand once, and later versions arrive by themselves.
x Updates come only from the app's GitHub release page. A computer that cannot reach GitHub (some office networks block it) gets them only by hand.
x The installer does not compare versions: running an older `Install.exe` by hand would replace a newer program. Keep the newest installer.
> If the data folder you chose cannot be found while updating (a drive that is not connected), the installer says so and offers to choose another; pressing Enter leaves the choice as it was, and the app will say it cannot reach the folder until the drive is back.

## Starting and closing

Start it from the desktop or Start-menu shortcut. It opens in a window of its own, with its own button on the taskbar; behind it runs a small server on your computer, at `http://127.0.0.1:8000` (or the next free port up to 8011 if that one is busy). Opening it again while it is open brings its window to the front rather than starting a second copy.

When it starts it reopens the run you had open last. About five seconds after starting it quietly loads the text-recognition engine in the background, so the first scan you add does not wait for it.

**Closing the window closes the app.** Everything is saved as you go, so there is nothing to save first. **Settings > Close the app** does the same, after asking *"Close the app now? Everything you have done is already saved."*

+ Everything is saved as you go; there is no Save button.
+ In the window, right-click copy and paste, Ctrl+F to find and Ctrl+P to print work as in a browser. A link that opens a file - a kept PDF, say - opens it in your browser.
x Without WebView2 the app opens in your browser, as versions before 2.2.0 always did, and there **closing the tab does not stop it**: use Settings > Close the app.

## Where your data is kept

Everything you do - every run, export register, decision and setting, and every file you give the app - lives in one **data folder**. By default that is the `data` folder inside the install folder. You can choose another:

- **At the first install**, type C at the question and pick a folder. If the folder you pick already has other things in it, the app makes a folder called *Assurance Console data* inside it and uses that, so its files are never mixed with yours - and erasing the data later can never take anything else with it.
- **Later, in Settings** - under *The app*, **Where your data is kept** shows the folder and how much it holds, with **Show the folder** (opens it in File Explorer) and **Move it to another folder…**. Moving asks you to pick a folder, copies everything there, checks the copy (the number of files and the bytes must match) and only then removes the old copy. The app carries on from the new folder straight away; no restart.
- **Every update** keeps the folder you chose, and says where it is.
- **Uninstalling** asks about that folder (see below).

!shot 13-settings | Settings, The app: the version and the update check, where the data is kept (Show the folder, Move it to another folder…), the switch that keeps every check, and Close the app.

The choice is written as one line in a file called `data-location.txt` beside the program. The installer, the app and the uninstaller all read it.

> If the data folder cannot be reached when the app starts - most often a USB or network drive that is not connected - the app does **not** start with an empty database. It says which folder it cannot reach and stops. Connect the drive and start again, or run `Install.exe` again to choose another folder.

What the folder holds:

| Item | What it is |
|---|---|
| `reconcile.db` | The database: runs, checks, decisions, settings, the supplier contact book, and what was read from every document |
| `reconcile.db-wal`, `reconcile.db-shm` | Working files while the app is open; folded into the database when it closes |
| `documents\` | Every file you gave the app, in a folder per run or register and a folder per kind inside it |
| `log.txt` | A record of unexpected errors, for troubleshooting |
| `reconcile.pre-v2.db` | Only on machines upgraded from a very early version: a copy of the old database made before it was converted |

## Backing up

x There is no automatic backup, and no backup or restore button.
+ Your whole working life in the app is the data folder. To back it up, close the app (Settings > Close the app), then copy the folder somewhere safe. To restore, put the copy back (or move the app to it in Settings).

Reports you download go to your Downloads folder (in a browser, to its download folder). They are copies: deleting a run, or uninstalling, does not delete them.

## Uninstalling

Use *Settings > Apps* in Windows, or **Uninstall Assurance Console** in the Start menu. A console window:

- shows where your data is and how big it is, and says it is kept by default: *"Type YES to erase them, or press Enter to keep"*. Only YES erases.
- closes the program if it is running, removes the shortcuts and the Add/Remove Programs entry, and deletes the program.
- If you **keep** the data, it stays where it is, with the note of where it is, so **installing again picks it up**.
- If you **erase** it, only what the app wrote into the data folder is removed (the database, its working files, `documents`, the log), and then the folder itself if that leaves it empty. Anything else you had put in that folder stays.
- Either way it removes the window's cache and any downloaded update, which are kept in `%LOCALAPPDATA%\Assurance Console`.

x The uninstaller closes every program named "Assurance Console.exe", not only the installed one.
x It leaves a small copy of itself in your temporary folder (Windows will not let a running program delete itself).
x Files in use can occasionally stop it removing everything; it lists them and asks you to restart and delete the folder by hand.
