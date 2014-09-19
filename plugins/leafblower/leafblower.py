#################################################################################################
#
# A plugin to help identify common POSIX functions such as printf, sprintf, memcmp, strcpy, etc.
#
# This plugin will really only work with RISC architectures, as it assumes a fixed instruction
# size and that function arguments are passed via registers.
#
#################################################################################################
import idc
import idaapi
import idautils

class LeafBlowerFunctionChooser(idaapi.Choose2):

    def __init__(self, lbobj):
        self.lb = lbobj

        idaapi.Choose2.__init__(self, self.lb.TITLE, self.lb.COLUMNS)
        self.icon = 41

        self.populate_items()

    def OnSelectLine(self, n):
        idc.Jump(idc.LocByName(self.items[n][0]))

    def OnGetSize(self):
        return len(self.items)

    def OnGetLine(self, n):
        return self.items[n]

    def OnRefresh(self, n):
        self.populate_items()
        return len(self.items)

    def OnClose(self):
        pass

    def populate_items(self):
        self.items = []

        for function in self.lb.functions:
            candidates = []

            for candidate in function.candidates:
                candidates.append(candidate)

            if function.xrefs:
                xrefs = str(function.xrefs)
            else:
                xrefs = "*"

            if function.argc is not None:
                argc = str(function.argc)
            else:
                argc = "*"

            if function.loop is None:
                loops = "*"
            else:
                loops = str(function.loop)

            name = idc.Name(function.start)

            self.items.append([name, xrefs, argc, loops, ','.join(candidates)])

    def show(self):
        return self.Show(modal=False)

class ArchitectureSettings(object):

    def __init__(self, **kwargs):
        for (k,v) in kwargs.iteritems():
            setattr(self, k, v)

class ArchitectureSpecific(object):
    '''
    Architecture specific configuration / code.
    '''

    # Architecture specific definitions go here.
    #
    #@name  - An arbitrary name describing the architecture. Currently unused.
    #@argv  - A list of register names used to pass function arguments.
    #@delay - The number of jump/branch delay slots used by the architecture (typcally 0).
    #@size  - The architecture's insturction size (assumes a fixed-width instruction set).
    ARCHES = [
                ArchitectureSettings(name="MIPS", argv=['$a0', '$a1', '$a2', '$a3'], delay=1, size=4),
                ArchitectureSettings(name="ARM", argv=['R0', 'R1', 'R2', 'R3'], delay=0, size=4),
             ]

    def __init__(self):
        # Get a list of the current processor module's registers
        self.registers = idaapi.ph_get_regnames()

        # Find the list of function argument registers that fit the current processor module
        # TODO: Probably better to look this up based on the processor module name, as it can't
        #       distinguish between ARM and Thumb.
        self.argv = None
        for arch in self.ARCHES:
            if not (set(arch.argv) - set(self.registers)):
                self.argv = list(arch.argv)
                self.delay_slot = arch.delay
                self.insn_size = arch.size
                break

        if self.argv is None:
            raise Exception("Unknown/unsupported architecture!")

class Prototype(object):

    def __init__(self, name, argc, argv=[], loop=True, fmtarg=None):
        self.name = name
        self.argc = argc
        self.loop = loop
        self.argv = argv
        self.fmtarg = fmtarg

class Function(object):
    '''
    A wrapper class for storing function related info.
    '''

    PROTOTYPES = [
                    Prototype(name="atoi", argc=1),
                    Prototype(name="strlen", argc=1),
                    Prototype(name="strcpy", argc=2),
                    Prototype(name="strcat", argc=2),
                    Prototype(name="strcmp", argc=2),
                    Prototype(name="strstr", argc=2),
                    Prototype(name="strchr", argc=2),
                    Prototype(name="strrchr", argc=2),
                    Prototype(name="bzero", argc=2),
                    Prototype(name="strtol", argc=3),
                    Prototype(name="strncpy", argc=3),
                    Prototype(name="strncmp", argc=3),
                    Prototype(name="memcpy", argc=3),
                    Prototype(name="memmove", argc=3),
                    Prototype(name="bcopy", argc=3),
                    Prototype(name="memcmp", argc=3),
                    Prototype(name="memset", argc=3),

                    Prototype(name="printf", argc=1, fmtarg=0),
                    Prototype(name="sprintf", argc=2, fmtarg=1),
                    Prototype(name="snprintf", argc=3, fmtarg=2),
                    Prototype(name="fprintf", argc=2, fmtarg=1),
                    Prototype(name="fscanf", argc=2, fmtarg=1),
                    Prototype(name="sscanf", argc=2, fmtarg=1),
                 ]

    def __init__(self, **kwargs):
        self.argc = None
        self.loop = None
        self.leaf = False
        self.xrefs = None
        self.fmtarg = None
        self.start = idc.BADADDR
        self.end = idc.BADADDR
        self.candidates = {}
        self.argp = ArgParser()

        for (k,v) in kwargs.iteritems():
            setattr(self, k, v)

        self.name = idc.Name(self.start)

        if self.xrefs is None:
            self.xrefs = len([x for x in idautils.XrefsTo(self.start)])

        if not self.candidates:
            for prototype in self.PROTOTYPES:
                if self.leaf and prototype.fmtarg is None and prototype.argc == self.argc and prototype.loop == self.loop:
                    if self.candidates.has_key(prototype.name):
                        self.candidates[prototype.name] += 1
                    else:
                        self.candidates[prototype.name] = 1
                elif not self.leaf and self.fmtarg is not None and prototype.fmtarg is not None and self.fmtarg == prototype.fmtarg:
                    if self.candidates.has_key(prototype.name):
                        self.candidates[prototype.name] += 1
                    else:
                        self.candidates[prototype.name] = 1

class ArgParser(object):
    '''
    Attempts to identify the number of arguments a function takes as well as
    what type of arguments a function takes.
    '''

    # An iterable list of canonical flags indicating an operand has been changed
    CHANGE_OPND = [
                    idaapi.CF_CHG1,
                    idaapi.CF_CHG2,
                    idaapi.CF_CHG3,
                    idaapi.CF_CHG4,
                    idaapi.CF_CHG5,
                    idaapi.CF_CHG6,
                  ]

    # An iterable list of canonical flags indicating an operand has been used
    USE_OPND = [
                    idaapi.CF_USE1,
                    idaapi.CF_USE2,
                    idaapi.CF_USE3,
                    idaapi.CF_USE4,
                    idaapi.CF_USE5,
                    idaapi.CF_USE6,
               ]

    def __init__(self):
        self.arch = ArchitectureSpecific()

    def argc(self, function):
        '''
        Counts the number of arguments used by the specified function.
        '''
        argv = set()
        notargv = set()
        ea = function.startEA

        while ea < function.endEA:
            idaapi.decode_insn(ea)
            features = idaapi.cmd.get_canon_feature()

            for n in range(0, len(self.USE_OPND)):
                if idaapi.cmd.Operands[n].type in [idaapi.o_reg, idaapi.o_displ, idaapi.o_phrase]:
                    try:
                        regname = self.arch.registers[idaapi.cmd.Operands[n].reg]
                        index = self.arch.argv.index(regname)
                    except ValueError:
                        continue

                    if features & self.USE_OPND[n] and regname not in notargv:
                        argv.update(self.arch.argv[:index+1])

            for n in range(0, len(self.CHANGE_OPND)):
                if idaapi.cmd.Operands[n].type in [idaapi.o_reg, idaapi.o_displ, idaapi.o_phrase]:
                    try:
                        regname = self.arch.registers[idaapi.cmd.Operands[n].reg]
                        index = self.arch.argv.index(regname)
                    except ValueError:
                        continue

                    if features & self.CHANGE_OPND[n] and regname not in argv:
                        notargv.update(self.arch.argv[index:])

            if argv.union(notargv) == set(self.arch.argv):
                break

            ea += self.arch.insn_size

        return len(argv)

    def trace(self, ea):
        '''
        Given an EA where an argument register is set, attempt to trace what
        function call that argument is passed to.

        @ea - The address of an instruction that modifies a function argument register.

        Returns a tuple of (function EA, argv index, argument register name) on success.
        Returns None on failure.
        '''
        idaapi.decode_insn(ea)
        features = idaapi.cmd.get_canon_feature()

        for n in range(0, len(self.CHANGE_OPND)):
            if idaapi.cmd.Operands[n].type in [idaapi.o_reg, idaapi.o_displ, idaapi.o_phrase]:
                try:
                    regname = self.arch.registers[idaapi.cmd.Operands[n].reg]
                    index = self.arch.argv.index(regname)
                except ValueError:
                    continue

                if features & self.CHANGE_OPND[n]:
                    ea = ea - (self.arch.delay_slot * self.arch.insn_size)

                    while True:
                        idaapi.decode_insn(ea)

                        if idaapi.is_call_insn(ea):
                            for xref in idautils.XrefsFrom(ea):
                                if xref.type in [idaapi.fl_CF, idaapi.fl_CN]:
                                    return (xref.to, index, regname)
                            # If we couldn't figure out where the function call was going to, just quit
                            break

                        if idaapi.is_basic_block_end(ea):
                            break

                        ea += self.arch.insn_size

        return (None, None, None)


    def argv(self, func):
        '''
        Attempts to identify what types of arguments are passed to a given function.
        Currently unused.
        '''
        args = [None for x in self.arch.argv]

        for xref in idautils.XrefsTo(func.startEA):
            if idaapi.is_call_insn(xref.frm):
                idaapi.decode_insn(xref.frm)

                ea = xref.frm + (self.arch.delay_slot * self.arch.insn_size)
                end_ea = (xref.frm - (self.arch.insn_size * 10))

                while ea >= end_ea:
                    # Stop searching if we've reached a conditional block or another call
                    if idaapi.is_basic_block_end(ea) or (ea != xref.frm and idaapi.is_call_insn(ea)):
                        break

                    idaapi.decode_insn(ea)
                    features = idaapi.cmd.get_canon_feature()

                    for n in range(0, len(self.CHANGE_OPND)):
                        if idaapi.cmd.Operands[n].type in [idaapi.o_reg, idaapi.o_displ, idaapi.o_phrase]:
                            try:
                                regname = self.arch.registers[idaapi.cmd.Operands[n].reg]
                                index = self.arch.argv.index(regname)
                            except ValueError:
                                continue

                            if features & self.CHANGE_OPND[n]:
                                for xref in idautils.XrefsFrom(ea):
                                    # TODO: Where is this xref type defined?
                                    if xref.type == 1:
                                        string = idc.GetString(xref.to)
                                        if string and len(string) > 4:
                                            args[index] = str
                                        break

                    ea -= self.arch.insn_size

                yield args

class LeafFunctionFinder(object):
    '''
    Class that searches for functions that do not call any other functions.
    '''

    TITLE = "Leaf functions"

    COLUMNS = [
                ["Function", 25 | idaapi.Choose2.CHCOL_PLAIN],
                ["Xrefs", 8 | idaapi.Choose2.CHCOL_PLAIN],
                ["argc", 8 | idaapi.Choose2.CHCOL_PLAIN],
                ["Has Loop(s)", 8 | idaapi.Choose2.CHCOL_PLAIN],
                ["Possible candidate(s)", 50 | idaapi.Choose2.CHCOL_PLAIN],
              ]

    def __init__(self):
        self.functions = []
        self.arch = ArchitectureSpecific()
        self.argp = ArgParser()
        self._find_leafs()

    def _find_leafs(self):
        # Loop through every function
        for func_ea in idautils.Functions():
            # Count the number of xrefs to this function
            func = idaapi.get_func(func_ea)
            if func:
                leaf_function = True
                ea = func.startEA

                # Loop through all instructions in this function looking
                # for call instructions; if found, then this is not a leaf.
                while ea <= func.endEA:
                    idaapi.decode_insn(ea)
                    if idaapi.is_call_insn(ea):
                        leaf_function = False
                        break

                    ea += self.arch.insn_size

                if leaf_function:
                    self.functions.append(Function(start=func.startEA,
                                                   end=func.endEA,
                                                   leaf=True,
                                                   loop=self.has_loop(func),
                                                   argc=self.argp.argc(func)))

        # Sort leafs by xref count, largest first
        self.functions.sort(key=lambda f: f.xrefs, reverse=True)

    def has_loop(self, func):
        '''
        A nieve method for checking to see if a function contains a loop.
        Works pretty well for simple functions though.
        '''
        blocks = [func.startEA] + [block.endEA for block in idaapi.FlowChart(func)]
        for block in blocks:
            for xref in idautils.XrefsTo(block):
                xref_func = idaapi.get_func(xref.frm)
                if xref_func and xref_func.startEA == func.startEA:
                    if xref.frm >= block:
                        return True
        return False

class FormatStringFunctionFinder(object):

    TITLE = "Format string functions"

    COLUMNS = [
                ["Function", 25 | idaapi.Choose2.CHCOL_PLAIN],
                ["Xrefs", 8 | idaapi.Choose2.CHCOL_PLAIN],
                ["Format string argv index", 15 | idaapi.Choose2.CHCOL_PLAIN],
                ["Has Loop(s)", 8 | idaapi.Choose2.CHCOL_PLAIN],
                ["Possible candidate(s)", 50 | idaapi.Choose2.CHCOL_PLAIN],
              ]
    def __init__(self):
        self.functions = []
        self.argp = ArgParser()
        self._find_format_string_functions()

    def _find_format_string_functions(self):
        processed_func_eas = set()

        for string in idautils.Strings():
            if '%' in str(string):
                for xref in idautils.XrefsTo(string.ea):
                    (func_ea, argn, regname) = self.argp.trace(xref.frm)
                    if func_ea is not None and func_ea not in processed_func_eas:
                        # Abuse argc here to show the argv index of the format string
                        self.functions.append(Function(start=func_ea, argc=argn, fmtarg=argn))
                        processed_func_eas.add(func_ea)

        # Sort format string functions by xref count, largest first
        self.functions.sort(key=lambda f: f.xrefs, reverse=True)

class leaf_blower_t(idaapi.plugin_t):

    flags = 0
    comment = "Assists in identifying common POSIX functions in RISC architectures"
    help = ''
    wanted_name = 'leafblower'
    wanted_hotkey = ''

    def init(self):
        self.leaf_context_menu = idaapi.add_menu_item("Search/", "leaf functions", "", 0, self.LeafFromMenu, (None,))
        self.stdio_context_menu = idaapi.add_menu_item("Search/", "format string functions", "", 0, self.FormatFromMenu, (None,))
        return idaapi.PLUGIN_KEEP

    def term(self):
        idaapi.del_menu_item(self.leaf_context_menu)
        idaapi.del_menu_item(self.stdio_context_menu)
        return None

    def run(self):
        pass

    def LeafFromMenu(self, arg):
        LeafBlowerFunctionChooser(LeafFunctionFinder()).show()

    def FormatFromMenu(self, arg):
        LeafBlowerFunctionChooser(FormatStringFunctionFinder()).show()

def PLUGIN_ENTRY():
    return leaf_blower_t()