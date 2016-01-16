# Importing Data

Once you've got Tabbycat [installed](../installation/intro.md), the next step is to import data for the tournament. The initial import requires details of all institutions, teams, speakers and adjudicators, and specifications for how many rounds and their properties. We don't currently have a way of doing this from the web interface. There are a few ways to do it.

> *__Note:__ With any method, the objective is to create one `Tournament` object, and `Round`, `Venue`, `Institution`, `Team`, `Speaker`, `Adjudicator`, `AdjudicatorConflict` and `AdjudicatorInstitutionConflict` objects for each round, venue, and so on*

## Adding Data Manually

For sufficiently small tournaments, you might just choose to edit the database via the Data Administrative interface.

> *__Caution!__ The Data Administrative interface is very powerful, and naturally if you mess things up, you can insert potentially catastrophic inconsistencies into the database. When you're setting up a tournament for the first time, this is highly unlikely to happen, but it's worth keeping in mind.*

1. Open up your the admin area of your site by going to the URL with /admin/ on the end, _e.g._ if your URL root is 127.0.0.1:8000, then [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/).
2. Then click **Debate** in the sidebar.
3. Create a new Tournament object, and input all of its rounds
4. Create the Venues
5. Create the Institutions
6. Create the Teams, and input their speakers
7. Create the Adjudicators, and input their conflicts

## Adding Data Using the Visual Importer

For medium sized tournaments there are a number of tools that allow you to import information by copy and pasting from spreadsheets. Or by adding data in bulk and editing key information later.

1. Open the "Data Import" area of the site using the link in the top right menu (once your have created a tournament and logged in)
2. Follow the instructions for each particular section

> *__Note:__ that if copy-pasting from a spreadsheet, it is often easiest to make values be separated by commas by saving the spreadsheet as a *.csv* file, then opening said file in a plain text editor (such as Notepad or TextEdit) and copy/pasting from there.*

## Adding Data with the `import_tournament` Command

We've written a management command called `import_tournament` to help automate the tournament set-up. The script, however, is neither foolproof nor comprehensive, so you might find you need to modify things slightly if your tournament structure is different from ours. Be prepared to try this a few times to get it to work. Nonetheless, this is probably the fastest way to set up a tournament.

1. Copy and rename the ```data/demo``` folder
2. See the CSV files in the new folder, and add/replace the data as per your tournament. Note that the institutions (*i.e.* first column) in the ```speakers.csv``` and ```adjudicators.csv``` files must match the institutions in the second column of the ```institutions.csv``` file. And that all CSV files must end with a blank line.
3. Use this command, replacing `YOUR_DATA_DIR` with your new folder's name. (Square brackets indicate optional arguments; if you use them, omit the square brackets. All of them relate to the name of your tournament.)

        $ ./manage.py import_tournament YOUR_DATA_DIR [--slug SLUG] [--short-name SHORT_NAME] [--name FULL_NAME]

    This script has a number of options. They're worth taking a look at before you run the script. For details, see:

        $ ./manage.py import_tournament --help

4. Assuming the command completes successfully without errors, you should double check the data in the Data Admin interface, as described above in *Manual setup.* In particular you should check that the *Rounds* have the correct draw types and that silent rounds have been marked correctly.

## `import_tournament` on Heroku Installs

The `import_tournament` script can be run on Heroku directly; you just need to commit and push your new data directory to your server first. See [Installing on Heroku](../installation/heroku.md) for details. If you have a local installation ready, you might like to iron out all the errors in your data until you can import locally without error, before pushing your data files to your server to be imported there.

If you want to import locally and push the database to the server, you can use the [`heroku pg:push`](https://devcenter.heroku.com/articles/heroku-postgresql#pg-push) command. We assume that, if you want to use this method, you know what you're doing or are comfortable reading the Heroku documentation to find out. We're happy to help if you have questions about this, but for most tournaments, committing the data to the server and running the script on the server directly will be easier.

## Writing your own importer

If our suggested file formats cause you headaches, it might be easier to write your own importer. We have a generic importer framework that should make this easier, so for some tournaments it might (very conceivably) be faster to write your own importer to conform to your data, than it is to make your data conform to our importer. You need a background in Python in order to do this. For more details, see [tournament data importers](../features/data-importers.md).