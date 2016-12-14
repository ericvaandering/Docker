#! /usr/bin/env python

from __future__ import print_function

import glob
import json
import os

import jinja2
import xunitparser

from github import Github

pylintReportFile = 'pylint.jinja'
pylintSummaryFile = 'pylintSummary.jinja'
unitTestSummaryFile = 'unitTestReport.jinja'

reportWarnings = ['0611', '0612', '0613']

summaryMessage = ''
longMessage = ''
reportOn = {}
failed = False


def buildPylintReport(templateEnv):
    with open('LatestPylint/pylintReport.json', 'r') as reportFile:
        report = json.load(reportFile)

        pylintReportTemplate = templateEnv.get_template(pylintReportFile)
        pylintSummaryTemplate = templateEnv.get_template(pylintSummaryFile)

        # Process the template to produce our final text.
        pylintReport = pylintReportTemplate.render({'report': report,
                                                    'filenames': sorted(report.keys()),
                                                    })
        pylintSummary = pylintSummaryTemplate.render({'report': report,
                                                      'filenames': sorted(report.keys()),
                                                      })

    # Figure out if pylint failed

    failed = False
    for filename in report.keys():
        for event in report[filename]['test']['events']:
            if event[1] in ['W', 'E']:
                failed = True

        if float(report[filename]['test']['score']) < 9 and (float(report[filename]['test']['score']) <
                                                                 float(report[filename]['test'].get('score', 0))):
            failed = True
        elif float(report[filename]['test']['score']) < 8:
            failed = True

    return failed, pylintSummary, pylintReport


def buildTestReport(templateEnv):
    unstableTests = []
    testResults = {}

    try:
        with open('UnstableTests.txt') as unstableFile:
            for line in unstableFile:
                unstableTests.append(line.strip())
    except:
        print("Was not able to open list of unstable tests")

    for kind, directory in [('base', './MasterUnitTests/'), ('test', './LatestUnitTests/')]:
        print("Scanning directory %s" % directory)
        for xunitFile in glob.iglob(directory + '*/nosetests-*.xml'):
            print("Opening file %s" % xunitFile)
            with open(xunitFile) as xf:
                ts, tr = xunitparser.parse(xf)
                for tc in ts:
                    testName = '%s:%s' % (tc.classname, tc.methodname)
                    if testName in testResults:
                        testResults[testName].update({kind: tc.result})
                    else:
                        testResults[testName] = {kind: tc.result}

    failed = False
    errorConditions = ['error', 'failure']

    newFailures = []
    unstableChanges = []
    okChanges = []
    added = []
    deleted = []

    for testName, testResult in sorted(testResults.items()):
        oldStatus = testResult.get('base', None)
        newStatus = testResult.get('test', None)
        if oldStatus and newStatus and testName in unstableTests:
            if oldStatus != newStatus:
                unstableChanges.append({'name': testName, 'new': newStatus, 'old': oldStatus})
        elif oldStatus and newStatus:
            if oldStatus != newStatus:
                if newStatus in errorConditions:
                    failed = True
                    newFailures.append({'name': testName, 'new': newStatus, 'old': oldStatus})
                else:
                    okChanges.append({'name': testName, 'new': newStatus, 'old': oldStatus})
        elif newStatus:
            added.append({'name': testName, 'new': newStatus, 'old': oldStatus})
            if newStatus in errorConditions:
                failed = True
        elif oldStatus:
            deleted.append({'name': testName, 'new': newStatus, 'old': oldStatus})

    changed = newFailures or added or deleted or unstableChanges or okChanges
    stableChanged = newFailures or added or deleted or okChanges

    unitTestSummaryTemplate = templateEnv.get_template(unitTestSummaryFile)
    unitTestSummaryHTML = unitTestSummaryTemplate.render({'newFailures': newFailures,
                                                          'added': added,
                                                          'deleted': deleted,
                                                          'unstableChanges': unstableChanges,
                                                          'okChanges': okChanges,
                                                          'errorConditions': errorConditions,
                                                          })

    unitTestSummary = {'newFailures': len(newFailures), 'added': len(added), 'deleted': len(deleted),
                       'okChanges': len(okChanges), 'unstableChanges': len(unstableChanges)}
    print("Unit Test summary %s" % unitTestSummary)
    return failed, unitTestSummaryHTML, unitTestSummary


templateLoader = jinja2.FileSystemLoader(searchpath="templates/")
templateEnv = jinja2.Environment(loader=templateLoader, trim_blocks=True, lstrip_blocks=True)

failedPylint = False
failedUnitTests = False

with open('artifacts/PullRequestReport.html', 'w') as html:
    failedPylint, pylintSummary, pylintReport = buildPylintReport(templateEnv)
    html.write(pylintSummary)
    html.write(pylintReport)

    failedUnitTests, unitTestSummaryHTML, unitTestSummary = buildTestReport(templateEnv)

    html.write(unitTestSummaryHTML)

gh = Github(os.environ['DMWMBOT_TOKEN'])
codeRepo = os.environ.get('CODE_REPO', 'WMCore')
teamName = os.environ.get('WMCORE_REPO', 'dmwm')
repoName = '%s/%s' % (teamName, codeRepo)

issueID = None

if 'ghprbPullId' in os.environ:
    issueID = os.environ['ghprbPullId']
    mode = 'PR'
elif 'TargetIssueID' in os.environ:
    issueID = os.environ['TargetIssueID']
    mode = 'Daily'

repo = gh.get_repo(repoName)
issue = repo.get_issue(int(issueID))
reportURL = os.environ['BUILD_URL'].replace('jenkins/job',
                                            'jenkins/view/All/job') + 'artifact/artifacts/PullRequestReport.html'

statusMap = {False: {'ghStatus': 'success', 'readStatus': 'succeeded'},
             True: {'ghStatus': 'failure', 'readStatus': 'failed'}, }

message = 'Jenkins results:\n'
message += ' * Unit tests: %s\n' % statusMap[failedUnitTests]['readStatus']
if unitTestSummary['newFailures']:
    message += '   * %s new failures\n' % unitTestSummary['newFailures']
if unitTestSummary['deleted']:
    message += '   * %s tests deleted\n' % unitTestSummary['deleted']
if unitTestSummary['okChanges']:
    message += '   * %s tests no longer failing\n' % unitTestSummary['okChanges']
if unitTestSummary['added']:
    message += '   * %s tests added\n' % unitTestSummary['added']
if unitTestSummary['unstableChanges']:
    message += '   * %s changes in unstable tests\n' % unitTestSummary['unstableChanges']
message += ' * Pylint check: %s\n' % statusMap[failedPylint]['readStatus']
message += "\nDetails at %s\n" % (reportURL)
status = issue.create_comment(message)

lastCommit = repo.get_pull(int(issueID)).get_commits().get_page(0)[-1]
lastCommit.create_status(state=statusMap[failedPylint]['ghStatus'], target_url=reportURL + '#pylint',
                         description='Set by Jenkins', context='Pylint')
lastCommit.create_status(state=statusMap[failedUnitTests]['ghStatus'], target_url=reportURL + '#unittests',
                         description='Set by Jenkins', context='Unit tests')
