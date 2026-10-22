"""
The tutorial script: what is on screen, and the words shown beneath it.

Written for someone who is not an accountant: every term is said in plain
words the first time, and the story is the one a first-time user lives - pick
a check, add two files, read the verdict, open an entry to see why it is open,
tick it off, download.

Scene durations are not written here. Each subtitle lasts as long as it takes
to read, and a scene as long as its subtitles, so re-wording a line re-times
the video. Avoid semicolons: the subtitle splitter treats them as full stops.

`shot` names a file in media/shots. `focus` crops to a region of it, given as
fractions of width and height (left, top, right, bottom), so a point can be
made about one part of the screen without the viewer hunting for it.
"""

TITLE = "Assurance Console"
SUBTITLE = "Every entry that does not agree, and why"

SCENES = [
    dict(kind="title", title=TITLE, sub=SUBTITLE,
         say="It checks your books against what someone else has on record – "
             "the GST portal, your bank, or a supplier – and tells you in plain "
             "words why anything does not agree. It all runs on this computer. "
             "Nothing is uploaded."),

    dict(kind="section", n="01", name="Pick a check",
         say="It starts on one screen."),
    dict(kind="screen", shot="01-start", label="Three kinds of check",
         say="There are three kinds of check. Your purchase bills, against what "
             "your suppliers filed on the GST portal. Your bank book, against the "
             "bank's statement. Or your ledger for one supplier or customer, "
             "against the statement they sent you."),

    dict(kind="section", n="02", name="Add two files",
         say="Each check needs two files that ought to agree."),
    dict(kind="screen", shot="02-files", label="Two files that should agree",
         say="For GST, that is the purchase register you export from Tally, and "
             "the GSTR-2B – the list of bills your suppliers filed – from the "
             "portal. A tick shows each file is in. Then press Check these files. "
             "Not ready with your own? Try it with sample files."),

    dict(kind="section", n="03", name="Read the verdict",
         say="The answer comes first, in one sentence."),
    dict(kind="screen", shot="03-verdict", label="The answer first",
         say="The headline says how many entries need a look. Under it are the "
             "figures that matter, in plain words. The credit you can safely claim "
             "now. The credit waiting on bills missing from your books. The last "
             "date to claim this year's credit."),
    dict(kind="screen", shot="04-list", label="One line, one reason",
         say="Below it is everything still open, one line each: the date, why it "
             "is open in a single sentence, and the amount. A red dot means money "
             "is at risk. Amber means it needs a look."),

    dict(kind="section", n="04", name="Why an entry is open",
         say="Open any line to read the whole reason."),
    dict(kind="screen", shot="05-entry", label="The full explanation",
         say="Pick a group in the margin – here, bills whose number differs – "
             "and open a line. It says what the entry is, what was compared, and "
             "what it means for you. Then exactly what to do – here, correct one "
             "bill number in Tally."),

    dict(kind="section", n="05", name="Tick it off",
         say="Dealt with it? Mark it as done."),
    dict(kind="screen", shot="06-ticked", label="Marked as done",
         say="The line turns grey, gets a tick and moves to the end of the list, "
             "and the counts in the margin drop by one. It stays ticked when you "
             "open the run again. Undo puts it back."),

    dict(kind="section", n="06", name="The bank",
         say="Next, the bank – in the same run."),
    dict(kind="screen", shot="07-add-bank", label="Add a check to this run",
         say="A run is one client and one period. Choose Add another check to "
             "this run, pick Bank, and add the bank's statement and your bank "
             "book. The statement can be Excel, CSV or a PDF – even a scan or a "
             "photo."),
    dict(kind="screen", shot="08-bank", label="It adds up",
         say="It opens with the gap between your books and the bank. Then it names "
             "every rupee of it – cheques not yet paid out, deposits not yet "
             "credited, charges not yet entered – until the two balances meet."),
    dict(kind="screen", shot="09-dot", label="Even a single dot",
         say="Every open line gets its own reason – even this one. ₹236 left the "
             "bank with nothing but a dot for a description."),
    dict(kind="screen", shot="09-dot", focus=(0.302, 0.200, 0.953, 0.622),
         label="Named, not summarised",
         say="So the explanation says exactly that: with no name or reference to "
             "go on, only the amount and the date could be compared, and your "
             "books have no payment of ₹236. What to do: find out what it paid "
             "for, and enter it."),

    dict(kind="section", n="07", name="A party's statement",
         say="A supplier's statement works the same way."),
    dict(kind="screen", shot="10-party", label="Their statement, your ledger",
         say="Add the statement a supplier or customer sent you, and your ledger "
             "for them. You see what each side says is owed, and every entry that "
             "makes up the difference."),

    dict(kind="section", n="08", name="Take it with you",
         say="Everything on screen can be downloaded."),
    dict(kind="screen", shot="11-downloads", label="Downloads",
         say="An Excel workbook, with a sheet giving the reason for every open "
             "entry. A PDF working paper to print and sign. Lists as CSV, and "
             "ready-to-send letters to suppliers about their missing bills."),
    dict(kind="screen", shot="12-runs", label="Every run is kept",
         say="Every run is saved on this computer. Open it again next month, add "
             "another check to it, or delete it."),
    dict(kind="screen", shot="13-settings", label="Settings",
         say="Settings holds your business details and how closely entries must "
             "agree. When you are finished, Close the app is here. Everything was "
             "saved as you went."),

    dict(kind="outro", title="Every entry, explained",
         sub="Checked on this computer. Nothing uploaded.",
         say="Pick a check, add two files, and read why anything does not agree. "
             "That is all there is to it."),
]
