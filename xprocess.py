from __future__ import division

import sys
import os
import warnings

from py import std
import psutil


class XProcessInfo:
    def __init__(self, path, name):
        self.name = name
        self.controldir = path.ensure(name, dir=1)
        self.logpath = self.controldir.join("xprocess.log")
        self.pidpath = self.controldir.join("xprocess.PID")
        if self.pidpath.check():
            self.pid = int(self.pidpath.read())
        else:
            self.pid = None

    def terminate(self):
        # return codes:
        # 0   no work to do
        # 1   terminated
        # -1  failed to terminate

        if not self.pid or not self.isrunning():
            return 0

        timeout = 20

        try:
            proc = psutil.Process(self.pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout/2)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout/2)
        except psutil.Error:
            return -1
        else:
            return 1

    def kill(self):
        warnings.warn("Use .terminate instead of .kill", DeprecationWarning, stacklevel=2)
        return self.terminate()

    def isrunning(self):
        if self.pid is None:
            return False
        try:
            proc = psutil.Process(self.pid)
        except psutil.NoSuchProcess:
            return False
        return proc.is_running()


class XProcess:
    def __init__(self, config, rootdir, log=None):
        self.config = config
        self.rootdir = rootdir
        if log is None:
            class Log:
                def debug(self, msg, *args):
                    if args:
                        print (msg % args)
                    else:
                        print (msg)
            log = Log()
        self.log = log

    def getinfo(self, name):
        """ return Process Info for the given external process. """
        return XProcessInfo(self.rootdir, name)

    def ensure(self, name, preparefunc, restart=False):
        """ returns (PID, logfile) from a newly started or already
            running process.

        @param name: name of the external process, used for caching info
                     across test runs.

        @param preparefunc:
                A subclass of ProcessStarter.

        @param restart: force restarting the process if it is running.

        @return: (PID, logfile) logfile will be seeked to the end if the
                 server was running, otherwise seeked to the line after
                 where the waitpattern matched.
        """
        from subprocess import Popen, STDOUT
        info = self.getinfo(name)
        if not restart and not info.isrunning():
            restart = True

        if restart:
            if info.pid is not None:
                info.terminate()
            controldir = info.controldir.ensure(dir=1)
            #controldir.remove()
            if not issubclass(preparefunc, ProcessStarter):
                preparefunc = ProcessStarter.wrap(preparefunc)
            starter = preparefunc(controldir, self)
            args = [str(x) for x in starter.args]
            self.log.debug("%s$ %s", controldir, " ".join(args))
            stdout = open(str(info.logpath), "wb", 0)
            kwargs = {'env': starter.env}
            if sys.platform == "win32":
                kwargs["startupinfo"] = sinfo = std.subprocess.STARTUPINFO()
                if sys.version_info >= (2,7):
                    sinfo.dwFlags |= std.subprocess.STARTF_USESHOWWINDOW
                    sinfo.wShowWindow |= std.subprocess.SW_HIDE
            else:
                kwargs["close_fds"] = True
                kwargs["preexec_fn"] = os.setpgrp  # no CONTROL-C
            popen = Popen(args, cwd=str(controldir),
                          stdout=stdout, stderr=STDOUT,
                          **kwargs)
            info.pid = pid = popen.pid
            info.pidpath.write(str(pid))
            self.log.debug("process %r started pid=%s", name, pid)
            stdout.close()
        f = info.logpath.open()
        if not restart:
            f.seek(0, 2)
        else:
            if starter.wait(f):
                self.log.debug("%s process startup detected", name)
            else:
                raise RuntimeError("Could not start process %s" % name)
        logfiles = self.config.__dict__.setdefault("_extlogfiles", {})
        logfiles[name] = f
        self.getinfo(name)
        return info.pid, info.logpath

    def _infos(self):
        return (
            self.getinfo(p.basename)
            for p in self.rootdir.listdir()
        )

    def _xkill(self, tw):
        ret = 0
        for info in self._infos():
            termret = info.terminate()
            ret = ret or (termret==1)
            if termret == 1:
                tw.line("%s %s TERMINATED" % (info.pid, info.name))
            elif termret == -1:
                tw.line("%s %s FAILED TO TERMINATE" % (info.pid, info.name))
            elif termret == 0:
                tw.line("%s %s NO PROCESS FOUND" % (info.pid, info.name))
        return ret

    def _xshow(self, tw):
        for info in self._infos():
            running = info.isrunning() and "LIVE" or "DEAD"
            tw.line("%s %s %s %s" %(info.pid, info.name, running,
                                        info.logpath,))
        return 0


class ProcessStarter(object):
    env = None
    """
    The environment in which to invoke the process.
    """

    def __init__(self, control_dir, process):
        self.control_dir = control_dir
        self.process = process

    @abc.abstractproperty
    def args(self):
        "The args to start the process"

    def wait(self, log_file):
        "Wait until the process is ready."
        lines = self.get_lines(log_file)

    @staticmethod
    def get_lines(log_file):
        while True:
            line = f.readline()
            if not line:
                std.time.sleep(0.1)



class CompatStarter(ProcessStarter):
    def __init__(self, preparefunc, control_dir, process):
        self.prep(*preparefunc(control_dir))
        super(CompatStarter, self).__init__(control_dir, process)

    def prep(self, wait, args, env=None):
        if callable(wait):
            self.wait = lambda lines: wait()
        else:
            self.wait = functools.partial(self.expect, pattern=wait)
        self.env = env
        self.args = args

    @classmethod
    def wrap(self, prepare_func):
        return functools.partial(CompatStarter, prepare_func)

    def expect(self, pattern, lines):
        pass

    def _checkpattern(self, f, pattern, count=50):
        while 1:
            line = f.readline()
            if not line:
                std.time.sleep(0.1)
            self.log.debug(line)
            if std.re.search(pattern, line):
                return True
            count -= 1
            if count < 0:
                return False
