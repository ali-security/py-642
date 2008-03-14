
""" reporter - different reporter for different purposes ;-)
    Still lacks:
    
    1. Hanging nodes are not done well
"""

import py

from py.__.test.terminal.out import getout
from py.__.test import repevent
from py.__.test import outcome
from py.__.misc.terminal_helper import ansi_print, get_terminal_width
from py.__.test.representation import Presenter, repr_pythonversion,\
     getrelpath

import sys

from time import time as now

def choose_reporter(reporterclass, config):
    option = config.option
    if option.startserver or option.runbrowser:
        from py.__.test.rsession.web import WebReporter
        return WebReporter
    if option.restreport:
        from py.__.test.rsession.rest import RestReporter
        return RestReporter
    else:
        return reporterclass

class TestReporter(object):
    """ Simple test reporter which tracks failures
    and also calls arbitrary provided function,
    useful for tests
    """
    def __init__(self, reportfun):
        self.reportfun = reportfun
        self.flag = False

    def report(self, event):
        if event.is_failure():
            self.flag = True
        self.reportfun(event)

    __call__ = report

    def was_failure(self):
        return self.flag

class AbstractReporter(object):
    def __init__(self, config, hosts):
        self.config = config
        self.hosts = hosts
        self.failed_tests_outcome = []
        self.skipped_tests_outcome = []
        self.out = getout(py.std.sys.stdout)
        self.presenter = Presenter(self.out, config)
        self.to_rsync = {}

    def get_item_name(self, event, colitem):
        return "/".join(colitem.listnames())
    
    def report(self, what):
        repfun = getattr(self, "report_" + what.__class__.__name__, 
                         self.report_unknown)
        try:
            return repfun(what)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            print "Internal reporting problem"
            excinfo = py.code.ExceptionInfo()
            for i in excinfo.traceback:
                print str(i)[2:-1]
            print excinfo
            # XXX reenable test before removing below line and
            # run it with raise
            raise

    __call__ = report
    
    def report_unknown(self, what):
        if self.config.option.verbose: 
            print "Unknown report: %s" % what
    
    def report_SendItem(self, item):
        address = item.host.hostname
        assert isinstance(item.host.hostname, str)
        if self.config.option.verbose: 
            print "Sending %s to %s" % (item.item,
                                        address)
    
    def report_HostRSyncing(self, item):
        hostrepr = self._hostrepr(item.host)
        if item.synced: 
            if (item.host.hostname == "localhost" and 
                item.root == item.remotepath):
                print "%15s: skipping inplace rsync of %r" %(
                        hostrepr, item.remotepath)
            else: 
                print "%15s: skip duplicate rsync to %r" % (
                        hostrepr, item.remotepath)
        else:
            print "%15s: rsync %r to remote %r" % (hostrepr, 
                                                   item.root.basename, 
                                                   item.remotepath)

    def report_HostGatewayReady(self, item):
        self.to_rsync[item.host] = len(item.roots)
        hostrepr = self._hostrepr(item.host)
        self.out.write("%15s: gateway initialised (remote topdir: %s)\n"\
                       % (hostrepr, item.host.gw_remotepath))

    def report_HostRSyncRootReady(self, item):
        self.to_rsync[item.host] -= 1
        if not self.to_rsync[item.host]:
            self._host_ready(item)
    
    def _host_ready(self, item):
        self.hosts_to_rsync -= 1
        hostrepr = self._hostrepr(item.host)
        if self.hosts_to_rsync:
            print "%15s: READY (still %d to go)" % (hostrepr, 
                                                    self.hosts_to_rsync)
        else:
            print "%15s: READY" % hostrepr 
    
    def report_TestStarted(self, item):
        topdir = item.config.topdir
        hostreprs = [self._hostrepr(host) for host in item.hosts]
        txt = " Test started, hosts: %s " % ", ".join(hostreprs)
        self.hosts_to_rsync = len(item.hosts)
        self.out.sep("=", txt)
        self.timestart = item.timestart
        self.out.write("local top directory: %s\n" % topdir)
        for i, root in py.builtin.enumerate(item.roots):
            outof = "%d/%d" %(i+1, len(item.roots))
            self.out.write("local RSync root [%s]: %s\n" % 
                           (outof, root))

    def report_RsyncFinished(self, item):
        self.timersync = item.time
    
    def report_ImmediateFailure(self, event):
        self.out.line()
        self.repr_failure(event.item, event.outcome)
    
    def report_TestFinished(self, item):
        self.out.line()
        assert hasattr(self, 'timestart')
        self.timeend = item.timeend
        self.skips()
        self.failures()
        if hasattr(self, 'nodes'): # XXX: Testing
            self.hangs()
        self.summary()
        return len(self.failed_tests_outcome) > 0

    report_InterruptedExecution = report_TestFinished
    report_CrashedExecution = report_TestFinished

    def hangs(self):
        h = []
        if self.config.option.exitfirst:
            # reporting hanging nodes in that case makes no sense at all
            # but we should share some code in all reporters than
            return
        for node in self.nodes:
            h += [(i, node.channel.gateway.sshaddress) for i in node.pending]
        if h:
            self.out.sep("=", " HANGING NODES ")
            for i, node in h:
                self.out.line("%s on %s" % (" ".join(i.listnames()), node))
    
    def failures(self):
        if self.failed_tests_outcome:
            self.out.sep("=", " FAILURES ")
        for event in self.failed_tests_outcome:
            if isinstance(event, repevent.ReceivedItemOutcome):
                host = self.gethost(event)
                self.out.sep('_', "%s on %s" % 
                    (" ".join(event.item.listnames()), host))
                if event.outcome.signal:
                    self.presenter.repr_item_info(event.item)
                    self.repr_signal(event.item, event.outcome)
                else:
                    self.repr_failure(event.item, event.outcome)
            else:
                self.out.sep('_', " ".join(event.item.listnames()))
                out = outcome.SerializableOutcome(excinfo=event.excinfo)
                self.repr_failure(event.item, outcome.ReprOutcome(out.make_repr()))

    def gethost(self, event):
        return event.host.hostname
    
    def repr_failure(self, item, outcome):
        excinfo = outcome.excinfo
        traceback = excinfo.traceback
        if not traceback: 
            self.out.line("empty traceback from item %r" % (item,)) 
            return

        handler = getattr(self.presenter, 'repr_failure_tb%s' % self.config.option.tbstyle)
        handler(item, excinfo, traceback, lambda: self.repr_out_err(outcome))

    def repr_out_err(self, outcome):
        if outcome.stdout:
            self.out.sep('-', " Captured process stdout: ")
            self.out.write(outcome.stdout)
        if outcome.stderr:
            self.out.sep('-', " Captured process stderr: ")
            self.out.write(outcome.stderr)

    def repr_signal(self, item, outcome):
        signal = outcome.signal
        self.out.line("Received signal: %d" % outcome.signal)
        self.repr_out_err(outcome)

    def _hostrepr(self, host):
        return host.hostid
    
    def skips(self):
        # XXX review and test below, fix too many lines of 
        #     skips that happen in the same file/lineno
        #     (this is correct in py-dist) 
        texts = {}
        for event in self.skipped_tests_outcome:
            colitem = event.item
            if isinstance(event, repevent.ReceivedItemOutcome):
                outcome = event.outcome
                text = outcome.skipped.value
                itemname = repr(outcome.skipped.traceback[-2]).split("\n")[0]
            elif isinstance(event, repevent.SkippedTryiter):
                text = str(event.excinfo.value)
                itemname = "/".join(colitem.listnames())
            if text not in texts:
                texts[text] = [itemname]
            else:
                texts[text].append(itemname)
            
        if texts:
            self.out.line()
            self.out.sep('_', 'reasons for skipped tests')
            for text, items in texts.items():
                for item in items:
                    self.out.line('Skipped in %s' % item)
                self.out.line("reason: %s" % text[1:-1])
                self.out.line()
    
    def summary(self):
        def gather(dic):
            # XXX hack to handle dicts & ints here, get rid of it
            if isinstance(dic, int):
                return dic
            total = 0
            for key, val in dic.iteritems():
                total += val
            return total
        
        def create_str(name, count):
            if count:
                return ", %d %s" % (count, name)
            return ""

        total_passed = gather(self.passed)
        total_failed = gather(self.failed)
        total_skipped = gather(self.skipped)
        total = total_passed + total_failed + total_skipped
        skipped_str = create_str("skipped", total_skipped)
        failed_str = create_str("failed", total_failed)
        self.out.line()
        self.print_summary(total, skipped_str, failed_str)
    
    def print_summary(self, total, skipped_str, failed_str):
        self.out.sep("=", " %d test run%s%s in %.2fs (rsync: %.2f)" % 
            (total, skipped_str, failed_str, self.timeend - self.timestart,
             self.timersync - self.timestart))
    
    def report_SkippedTryiter(self, event):
        #event.outcome.excinfo.source = 
        self.skipped_tests_outcome.append(event)
    
    def report_FailedTryiter(self, event):
        pass
        # XXX: right now we do not do anything with it
    
    def report_ReceivedItemOutcome(self, event):
        host = event.host
        hostrepr = self._hostrepr(host)
        if event.outcome.passed:
            self.passed[host] += 1
            sys.stdout.write("%15s: PASSED  " % hostrepr)
        elif event.outcome.skipped:
            self.skipped_tests_outcome.append(event)
            self.skipped[host] += 1
            sys.stdout.write("%15s: SKIPPED " % hostrepr) 
        else:
            self.failed[host] += 1
            self.failed_tests_outcome.append(event)
            sys.stdout.write("%15s: " % hostrepr) 
            ansi_print("FAILED", esc=(31,1), newline=False, file=sys.stdout)
            sys.stdout.write("  ")
        # we should have printed 20 characters to this point
        itempath = ".".join(event.item.listnames()[1:-1])
        funname = event.item.listnames()[-1]
        lgt = get_terminal_width() - 20
        # mark the function name, to be sure
        to_display = len(itempath) + len(funname) + 1
        if to_display > lgt:
            sys.stdout.write("..." + itempath[to_display-lgt+4:])
        else:
            sys.stdout.write(itempath)
        sys.stdout.write(" ")
        ansi_print(funname, esc=32, file=sys.stdout)
    
    def report_Nodes(self, event):
        self.nodes = event.nodes

    def was_failure(self):
        return sum(self.failed.values()) > 0

class RemoteReporter(AbstractReporter):
    def __init__(self, config, hosts):
        super(RemoteReporter, self).__init__(config, hosts)
        self.failed = dict([(host, 0) for host in hosts])
        self.skipped = dict([(host, 0) for host in hosts])
        self.passed = dict([(host, 0) for host in hosts])
    
    def get_item_name(self, event, colitem):
        return event.host.hostname + ":" + \
            "/".join(colitem.listnames())
        
    def report_FailedTryiter(self, event):
        self.out.line("FAILED TO LOAD MODULE: %s\n" % "/".join(event.item.listnames()))
        self.failed_tests_outcome.append(event)
        # argh! bad hack, need to fix it
        self.failed[self.hosts[0]] += 1
    
    def report_SkippedTryiter(self, event):
        self.out.line("Skipped (%s) %s\n" % (str(event.excinfo.value), "/".
            join(event.item.listnames())))

class LocalReporter(AbstractReporter):
    def __init__(self, config, hosts=None):
        assert not hosts
        super(LocalReporter, self).__init__(config, hosts)
        self.failed = 0
        self.skipped = 0
        self.passed = 0

    def report_TestStarted(self, item):
        colitems = item.config.getcolitems()
        txt = " test process starts "
        self.out.sep("=", txt)
        self.timestart = item.timestart
        self.out.line("executable:   %s  (%s)" %
                      (py.std.sys.executable, repr_pythonversion()))
        rev = py.__pkg__.getrev()
        self.out.line("using py lib: %s <rev %s>" % (
                       py.path.local(py.__file__).dirpath(), rev))
        config = item.config
        if config.option.traceconfig or config.option.verbose: 
            
            for x in colitems: 
                self.out.line("test target:  %s" %(x.fspath,))

            conftestmodules = config._conftest.getconftestmodules(None)
            for i,x in py.builtin.enumerate(conftestmodules):
                self.out.line("initial conf %d: %s" %(i, x.__file__)) 

    def get_item_name(self, event, colitem):
        return "/".join(colitem.listnames())

    def print_summary(self, total, skipped_str, failed_str):
        self.out.sep("=", " %d test run%s%s in %.2fs" % 
            (total, skipped_str, failed_str, self.timeend - self.timestart))

    def report_SkippedTryiter(self, event):
        #self.show_item(event.item, False)
        if isinstance(event.item, py.test.collect.Module):
            self.out.write("- skipped (%s)" % event.excinfo.value)
        else:
            self.out.write("s")
            self.skipped_tests_outcome.append(event)
    
    def report_FailedTryiter(self, event):
        #self.show_item(event.item, False)
        self.out.write("- FAILED TO LOAD MODULE")
        self.failed_tests_outcome.append(event)
        self.failed += 1
    
    def report_ReceivedItemOutcome(self, event):
        if event.outcome.passed:
            self.passed += 1
            self.out.write(".")
        elif event.outcome.skipped:
            self.skipped_tests_outcome.append(event)
            self.skipped += 1
            self.out.write("s")
        else:
            self.failed += 1
            self.failed_tests_outcome.append(event)
            self.out.write("F")
    
    def report_ItemStart(self, event):
        # XXX
        event.item.start = now()
        self.show_item(event.item)
    
    def show_item(self, item, count_elems = True):
        if isinstance(item, py.test.collect.Module):
            self.show_Module(item)
        if self.config.option.verbose > 0 and\
           isinstance(item, py.test.collect.Item):
            self.show_ItemVerbose(item)

    def show_ItemVerbose(self, item):
        realpath, lineno = item._getpathlineno()
        location = "%s:%d" % (realpath.basename, lineno+1)
        self.out.write("%-20s %s " % (location, item._getmodpath()))

    def show_Module(self, mod):
        lgt = len(list(mod._tryiter()))
        if self.config.option.verbose == 0:
            base = getrelpath(py.path.local(), mod.fspath)
            self.out.write("\n%s[%d] " % (base, lgt))
        else:
            self.out.line()
            self.out.line('+ testmodule: %s[%d]' % (mod.fspath, lgt))

    def gethost(self, event):
        return 'localhost'
    
    def hangs(self):
        pass

    def was_failure(self):
        return self.failed > 0