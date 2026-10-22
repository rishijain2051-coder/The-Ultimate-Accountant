# When something goes wrong

## Messages and what to do

| You see | What it means | What to do |
|---|---|---|
| *That file cannot be read here* | The file's type is not accepted in that box | GST boxes take `.xlsx` only; bank and party boxes take Excel, CSV, PDF or a picture; Exports takes PDF, pictures, Excel, CSV and the GSTR-1 JSON |
| *That file is not a readable .xlsx workbook…* | Not a real .xlsx (an old .xls, a CSV renamed, a damaged download) | Download it again from the portal, or export again from Tally as Excel |
| *Could not find the column headers in this Tally export…* | No heading row with Particulars, Vch No. or Vch Type in the first 30 rows | Export the Purchase Register with Date, Particulars, Vch Type, Vch No., Debit and Credit |
| *No GSTR-2B rows could be read…* | No recognisable B2B (or other) sheet, or no GSTIN column | Use the GSTR-2B Excel from the portal, unedited |
| *Could not find the columns of that bank statement…* | No heading with a date, a narration and amounts | Download the statement as Excel or CSV from net banking if you can; for a PDF, try *Read the statement as a scan* |
| *Could not find the columns of that statement…* | The party file has no heading with date, particulars, and debit and credit (or one amount with a Dr/Cr column) | Ask for the statement as Excel, or export your ledger with Debit and Credit columns |
| *The running balance does not tie on N row(s)…* | Balances on the statement do not follow line to line: edited, or a figure misread | Look at the flagged rows against the original; re-download the statement if it came from net banking |
| *₹X of the difference cannot be explained* | The working does not add up | A figure was probably read wrongly - check the scan, or use an Excel copy |
| *That is an image, so it has to be read by recognition, and OCR is not installed* | Only when running from source without the recognition package | The installed program always has it; from source, install the requirements |
| *That is an old .xls file…* | An Excel 97-2003 file | Open it in Excel and Save As .xlsx |
| *Ports 8000 to 8011 are all in use* | Other programs are using all twelve (a copy of the app already open is brought to the front instead) | Close what is using those ports and start the app again |
| *Could not check - no internet connection* (Settings) | The computer was offline, or GitHub could not be reached | Nothing to do: it looks again at the next check. *Check now* tries straight away |
| *Could not check. The release file failed its signature check…* | The file on the release page was not signed with the publisher's key | Nothing was downloaded or installed. Tell whoever supplies the app |
| *Could not check. The download did not match the release's fingerprint…* | The installer fetched was not the one the release describes (a broken download, or tampering) | It was thrown away; the next check fetches it again |
| *No valid signing key is set (UA_UPDATE_PUBLIC_KEY)…* | This copy was built without its publisher's public key, so no release can be believed | Install a copy built with `UA_UPDATE_REPO` and `UA_UPDATE_PUBLIC_KEY` set, or update by hand |
| *Your data is kept in … and that folder cannot be reached* | The chosen data folder is on a drive that is not connected, or was deleted | Connect the drive and start again, or run Install.exe again to choose another folder |
| *That run no longer exists, so nothing was added to it* | The run was deleted in another tab | Start the check as a new run |
| *This check was not saved… nothing to download* | *Keep every check* is off in Settings | Switch it on and check the files again |
| *Nothing outstanding, so there is no follow-up pack to build* | No supplier has open bills | - |
| *No payment is waiting for an eBRC…* and other Exports refusals | There is nothing to put in that draft | - |
| *… is not a date*, *… is not a number*, *… has no invoice number* (correcting a document) | A box in the correcting window holds something that cannot be right | Put that box right, or blank it, and save again - nothing was saved until every box passes |
| *A new row cannot be added here* | Rows were added to your own register in the app | Add the row to the workbook itself and add the workbook again; its rows can be corrected or removed here |
| A document you added is not in the register | It is waiting under *Check what was read* at the top of the register | Check it and press *Post it* (or *Post all*) |

## Common questions

**It is slow the first time I add a scan.** From 2.1.2 the recognition engine is loaded in the background a few seconds after start; a very early scan may still wait for it. After that a scanned page takes about a second or a second and a half. Large scans take proportionately longer - a 40-page statement takes about a minute.

**The figures from my scan look wrong.** Scans are read, not trusted: check the flagged rows, the running-balance note and the residual. A statement downloaded as Excel or CSV from net banking is always better than a scan.

**The app opened in my browser, not in a window of its own.** WebView2 is missing or would not start. Install the *Microsoft Edge WebView2 Runtime* from Microsoft's website and open the app again. Until then it works in the browser, where closing the tab does not stop it - use Settings > Close the app.

**I pressed Restart and update and the app did not come back.** Open it from the shortcut. If the version in Settings is still the old one, the update did not finish; the installer it fetched is in `%LOCALAPPDATA%\Assurance Console\updates` - run it, or the newest `Install.exe` from the release page.

**The AI checker says a document reads differently.** Open the original and look at the field it names. If the AI is right, press *Use the AI's reading*; if the app is right, *The app read it right - take this off the list*. Either way the register goes on counting what the app read until you choose.

**Getting the AI checker stopped part-way.** Press *Try again* in Settings > AI checker: it carries on where it stopped. It needs about 8 GB free on the drive of your Windows user folder.

**I have Invoice Bridge too. Does the AI come down twice?** No. Whichever app gets it first keeps the one copy and the other uses it: *Settings > AI checker* then says *Uses the AI that Invoice Bridge downloaded*. If both already have a copy, the checker says so, and removing this one frees about 4 GB.

**Where are my reports?** In your Downloads folder, named after the business, the kind of report and the date and time.

**Where is my data?** Settings > The app > Where your data is kept. *Show the folder* opens it.

**How do I move my data to another drive?** Settings > The app > *Move it to another folder…* - the app copies everything, checks the copy and carries on from there. The installer and uninstaller follow it.

**How do I give someone a file?** Open the check or register, find the file under *Downloads and files* (or *Documents* in Exports), and use *Save a copy*, *Share*, or *Show in folder* to drag it into an email. An invoice's papers go together with *Save them as one zip*.

**Can two people use it?** Not at the same time on different computers. It is one person, one computer.

**Something failed that I cannot explain.** Unexpected errors are written, with details, to `log.txt` in the data folder. Send that file to whoever supports you. (Errors the app turns into a message for you are not written there.)

**Can I check the program itself?** From the source code, `python -m app.backend.selftest` runs seventy-nine checks of the matching, the credit working, the readers, the explanations, the export rules and the update signature; `python packaging/test_update.py` checks the installer's update, rollback, reopening and data-folder handling.
