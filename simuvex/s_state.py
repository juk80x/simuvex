#!/usr/bin/env python

import copy
import functools
import itertools
#import weakref

import logging
l = logging.getLogger("simuvex.s_state")

def arch_overrideable(f):
    @functools.wraps(f)
    def wrapped_f(self, *args, **kwargs):
        if hasattr(self.arch, f.__name__):
            arch_f = getattr(self.arch, f.__name__)
            return arch_f(self, *args, **kwargs)
        else:
            return f(self, *args, **kwargs)
    return wrapped_f

default_plugins = { }

# This is a base class for SimState plugins. A SimState plugin will be copied along with the state when the state is branched. They
# are intended to be used for things such as tracking open files, tracking heap details, and providing storage and persistence for SimProcedures.
class SimStatePlugin(object):
    #__slots__ = [ 'state' ]

    def __init__(self):
        self.state = None

    # Sets a new state (for example, if it the state has been branched)
    def set_state(self, state):
        #if type(state).__name__ == 'weakproxy':
        self.state = state
        #else:
        #   self.state = weakref.proxy(state)

    # Should return a copy of the state plugin.
    def copy(self):
        raise Exception("copy() not implement for %s", self.__class__.__name__)

    def merge(self, others, merge_flag, flag_values): # pylint: disable=W0613
        '''
        Should merge the state plugin with the provided others.

           others - the other state plugin
           merge_flag - a symbolic expression for the merge flag
           flag_values - the values to compare against to check which content should be used.

               self.symbolic_content = self.state.se.If(merge_flag == flag_values[0], self.symbolic_content, other.se.symbolic_content)

            Can return a sequence of constraints to be added to the state.
        '''
        raise Exception("merge() not implement for %s", self.__class__.__name__)

    @staticmethod
    def register_default(name, cls):
        if name in default_plugins:
            raise Exception("%s is already set as the default for %s" % (default_plugins[name], name))
        default_plugins[name] = cls

# This is a counter for the state-merging symbolic variables
merge_counter = itertools.count()

class SimState(object): # pylint: disable=R0904
    '''The SimState represents the state of a program, including its memory, registers, and so forth.'''

    def __init__(self, solver_engine, temps=None, arch="AMD64", plugins=None, memory_backer=None, mode=None, options=None):
        # the architecture is used for function simulations (autorets) and the bitness
        self.arch = Architectures[arch]() if isinstance(arch, str) else arch

        # the solving engine
        self._engine = solver_engine

        # VEX temps are temporary variables local to an IRSB
        self.temps = temps if temps is not None else { }

        # plugins
        self.plugins = { }
        if plugins is not None:
            for n,p in plugins.iteritems():
                self.register_plugin(n, p)

        if not self.has_plugin('memory'):
            self['memory'] = SimMemory(memory_backer, memory_id="mem")
        if not self.has_plugin('registers'):
            self['registers'] = SimMemory(memory_id="reg")

        if options is None:
            if mode is None:
                l.warning("SimState defaulting to static mode.")
                mode = "static"
            options = set(o.default_options[mode])

        self.options = options
        self.mode = mode

        # the native environment for native execution
        self.native_env = None

    # accessors for memory and registers and such
    @property
    def memory(self):
        return self['memory']

    @property
    def registers(self):
        return self['registers']

    @property
    def se(self):
        return self['solver_engine']

    @property
    def inspect(self):
        return self['inspector']

    def _inspect(self, *args, **kwargs):
        if self.has_plugin('inspector'):
            self.inspect.action(*args, **kwargs)

    #
    # Plugins
    #

    def has_plugin(self, name):
        return name in self.plugins

    def get_plugin(self, name):
        if name not in self.plugins:
            p = default_plugins[name]()
            self.register_plugin(name, p)
            return p
        return self.plugins[name]

    # ok, ok
    def __getitem__(self, name): return self.get_plugin(name)
    def __setitem__(self, name, plugin): return self.register_plugin(name, plugin)

    def register_plugin(self, name, plugin):
        #l.debug("Adding plugin %s of type %s", name, plugin.__class__.__name__)
        plugin.set_state(self)
        self.plugins[name] = plugin

    def release_plugin(self, name):
        if name in self.plugins:
            del self.plugins[name]

    #
    # Constraint pass-throughs
    #

    def simplify(self, *args): return self.se.simplify(*args)

    def add_constraints(self, *args):
        if len(args) > 0 and type(args[0]) in (list, tuple):
            raise Exception("Tuple or list passed to add_constraints!")

        if o.TRACK_CONSTRAINTS in self.options and len(args) > 0:
            self._inspect('constraints', BP_BEFORE, added_constraints=args)
            self.se.add(*args)
            self._inspect('constraints', BP_AFTER)

    def BV(self, name, size):
        size = self.arch.bits if size is None else size

        self._inspect('symbolic_variable', BP_BEFORE, symbolic_name=name, symbolic_size=size)
        v = self.se.BitVec(name, size)
        self._inspect('symbolic_variable', BP_AFTER, symbolic_expr=v)
        return v

    def BVV(self, value, size=None):
        if type(value) is str:
            v = 0
            for c in value:
                v = v << 8
                v += ord(c)
            size = len(value)*8
            value = v
        size = self.arch.bits if size is None else size
        return self.se.BitVecVal(value, size)

    def satisfiable(self):
        return self.se.satisfiable()

    def downsize(self):
        if 'solver_engine' in self.plugins:
            self.se.downsize()

    #
    # Memory helpers
    #

    # Helper function for loading from symbolic memory and tracking constraints
    def _do_load(self, simmem, addr, length, strategy=None, limit=None):
        # do the load and track the constraints
        m,e = simmem.load(addr, length, strategy=strategy, limit=limit)
        self.add_constraints(*e)
        return m

    # Helper function for storing to symbolic memory and tracking constraints
    def _do_store(self, simmem, addr, content, symbolic_length=None, strategy=None, limit=None):
        # do the store and track the constraints
        e = simmem.store(addr, content, symbolic_length=symbolic_length, strategy=strategy, limit=limit)
        self.add_constraints(*e)
        return e

    #
    # State branching operations
    #

    # Returns a dict that is a copy of all the state's plugins
    def copy_plugins(self):
        return { n: p.copy() for n,p in self.plugins.iteritems() }

    def copy(self):
        '''
        Returns a copy of the state.
        '''

        c_temps = copy.copy(self.temps)
        c_arch = self.arch
        c_plugins = self.copy_plugins()
        return SimState(self._engine, temps=c_temps, arch=c_arch, plugins=c_plugins, options=self.options, mode=self.mode)

    # Merges this state with the other states. Returns the merged state and the merge flag.
    def merge(self, *others):
        # TODO: maybe make the length of this smaller? Maybe: math.ceil(math.log(len(others)+1, 2))
        merge_flag = self.se.BitVec("state_merge_%d" % merge_counter.next(), 16)
        merge_values = range(len(others)+1)

        if len(set(frozenset(o.plugins.keys()) for o in others)) != 1:
            raise SimMergeError("Unable to merge due to different sets of plugins.")
        if len(set(o.arch.name for o in others)) != 1:
            raise SimMergeError("Unable to merge due to different architectures.")

        merged = self.copy()

        # plugins
        m_constraints = [ ]
        for p in self.plugins:
            m_constraints += merged.plugins[p].merge([ _.plugins[p] for _ in others ], merge_flag, merge_values)
        merged.add_constraints(*m_constraints)
        return merged, merge_flag

    #############################################
    ### Accessors for tmps, registers, memory ###
    #############################################

    # Returns the BitVector expression of a VEX temp value
    def tmp_expr(self, tmp):
        self._inspect('tmp_read', BP_BEFORE, tmp_read_num=tmp)
        v = self.temps[tmp]
        self._inspect('tmp_read', BP_AFTER, tmp_read_expr=v)
        return v

    # Stores a BitVector expression in a VEX temp value
    def store_tmp(self, tmp, content):
        self._inspect('tmp_write', BP_BEFORE, tmp_write_num=tmp, tmp_write_expr=content)

        if tmp not in self.temps:
            # Non-symbolic
            self.temps[tmp] = content
        else:
            # Symbolic
            self.add_constraints(self.temps[tmp] == content)

        self._inspect('tmp_write', BP_AFTER)

    # Returns the BitVector expression of the content of a register
    def reg_expr(self, offset, length=None, endness=None):
        if length is None: length = self.arch.bits / 8
        self._inspect('reg_read', BP_BEFORE, reg_read_offset=offset, reg_read_length=length)

        if type(offset) is str:
            offset,length = self.arch.registers[offset]

        e = self._do_load(self.registers, offset, length)

        if endness is None: endness = self.arch.register_endness
        if endness == "Iend_LE": e = e.reversed()

        self._inspect('reg_read', BP_AFTER, reg_read_expr=e)
        return e

    # Returns a concretized value of the content in a register
    def reg_concrete(self, *args, **kwargs):
        e = self.reg_expr(*args, **kwargs)
        if self.se.symbolic(e):
            raise SimValueError("target of reg_concrete is symbolic!")
        return self.se.any_int(e)

    # Stores a bitvector expression in a register
    def store_reg(self, offset, content, length=None, endness=None):
        if type(offset) is str:
            offset,length = self.arch.registers[offset]

        if type(content) in (int, long):
            if not length:
                l.warning("Length not provided to store_reg with integer content. Assuming bit-width of CPU.")
                length = self.arch.bits / 8
            content = self.se.BitVecVal(content, length * 8)

        if endness is None: endness = self.arch.register_endness
        if endness == "Iend_LE": content = content.reversed()

        self._inspect('reg_write', BP_BEFORE, reg_write_offset=offset, reg_write_expr=content, reg_write_length=content.size()/8) # pylint: disable=maybe-no-member
        e = self._do_store(self.registers, offset, content)
        self._inspect('reg_write', BP_AFTER)

        return e

    # Returns the BitVector expression of the content of memory at an address
    def mem_expr(self, addr, length, endness=None):
        if endness is None: endness = "Iend_BE"

        self._inspect('mem_read', BP_BEFORE, mem_read_address=addr, mem_read_length=length)

        e = self._do_load(self.memory, addr, length)
        if endness == "Iend_LE": e = e.reversed()

        self._inspect('mem_read', BP_AFTER, mem_read_expr=e)
        return e

    # Returns a concretized value of the content at a memory address
    def mem_concrete(self, *args, **kwargs):
        e = self.mem_expr(*args, **kwargs)
        if self.se.symbolic(e):
            raise SimValueError("target of mem_concrete is symbolic!")
        return self.se.any_int(e)

    # Stores a bitvector expression at an address in memory
    def store_mem(self, addr, content, symbolic_length=None, endness=None, strategy=None, limit=None):
        if endness is None: endness = "Iend_BE"
        if endness == "Iend_LE": content = content.reversed()

        self._inspect('mem_write', BP_BEFORE, mem_write_address=addr, mem_write_expr=content, mem_write_length=self.se.BitVecVal(content.size()/8, self.arch.bits) if symbolic_length is None else symbolic_length) # pylint: disable=maybe-no-member
        e = self._do_store(self.memory, addr, content, symbolic_length=symbolic_length, strategy=strategy, limit=limit)
        self._inspect('mem_write', BP_AFTER)

        return e

    ###############################
    ### Stack operation helpers ###
    ###############################

    @arch_overrideable
    def sp_expr(self):
        return self.reg_expr(self.arch.sp_offset)

    # Push to the stack, writing the thing to memory and adjusting the stack pointer.
    @arch_overrideable
    def stack_push(self, thing):
        # increment sp
        sp = self.reg_expr(self.arch.sp_offset) + 4
        self.store_reg(self.arch.sp_offset, sp)

        return self.store_mem(sp, thing, endness=self.arch.memory_endness)

    # Pop from the stack, adjusting the stack pointer and returning the popped thing.
    @arch_overrideable
    def stack_pop(self):
        sp = self.reg_expr(self.arch.sp_offset)
        self.store_reg(self.arch.sp_offset, sp - self.arch.bits / 8)

        return self.mem_expr(sp, self.arch.bits / 8, endness=self.arch.memory_endness)

    # Read some number of bytes from the stack at the provided offset.
    @arch_overrideable
    def stack_read(self, offset, length, bp=False):
        if bp:
            sp = self.reg_expr(self.arch.bp_offset)
        else:
            sp = self.reg_expr(self.arch.sp_offset)

        return self.mem_expr(sp+offset, length, endness=self.arch.memory_endness)

    ###############################
    ### Other helpful functions ###
    ###############################

    # Concretizes an expression and updates the state with a constraint making it that value. Returns a BitVecVal of the concrete value.
    def make_concrete(self, expr):
        if type(expr) in (int, long):
            raise ValueError("expr should not be an int or a long in make_concrete()")

        if not self.se.symbolic(expr):
            return expr

        v = self.se.any(expr)
        self.add_constraints(expr == v)
        return v

    def make_concrete_int(self, expr):
        if type(expr) in (int, long):
            return expr
        return self.se.any_int(self.make_concrete(expr))

    # This handles the preparation of concrete function launches from abstract functions.
    @arch_overrideable
    def prepare_callsite(self, retval, args, convention='wtf'):
        #TODO
        pass

    def _dbg_print_stack(self, depth=None):
        '''
        Only used for debugging purposes.
        Return the current stack info in formatted string. If depth is None, the
        current stack frame (from sp to bp) will be printed out.
        '''
        result = ""
        var_size = self.arch.bits / 8
        sp_sim = self.reg_expr(self.arch.sp_offset)
        bp_sim = self.reg_expr(self.arch.bp_offset)
        if self.se.symbolic(sp_sim):
            result = "SP is SYMBOLIC"
        elif self.se.symbolic(bp_sim):
            result = "BP is SYMBOLIC"
        else:
            sp_value = self.se.any_int(sp_sim)
            bp_value = self.se.any_int(bp_sim)
            result = "SP = 0x%08x, BP = 0x%08x\n" % (sp_value, bp_value)
            if depth == None:
                depth = (bp_value - sp_value) / var_size + 1 # Print one more value
            pointer_value = sp_value
            for i in range(depth):
                stack_value = self.stack_read(i * var_size, var_size, bp=False)

                if self.se.symbolic(stack_value):
                    concretized_value = "SYMBOLIC"
                else:
                    concretized_value = "0x%08x" % self.se.any_int(stack_value)

                if pointer_value == sp_value:
                    line = "(sp)% 16x | %s" % (pointer_value, concretized_value)
                elif pointer_value == bp_value:
                    line = "(bp)% 16x | %s" % (pointer_value, concretized_value)
                else:
                    line = "% 20x | %s" % (pointer_value, concretized_value)

                pointer_value += var_size
                result += line + "\n"
        return result

    def __getstate__(self):
        state = { }

        for i in [ 'arch', 'temps', 'memory', 'registers', 'plugins', 'track_constraints', 'options', 'mode' ]:
            state[i] = getattr(self, i, None)
            state['_solver'] = None

        return state

    #
    # Concretization
    #

    #def is_native(self):
    #   if self.native_env is None and o.NATIVE_EXECUTION not in self.options:
    #       l.debug("Not native, all good.")
    #       return False
    #   elif self.native_env is not None and o.NATIVE_EXECUTION in self.options:
    #       l.debug("Native, all good.")
    #       return True
    #   elif self.native_env is None and o.NATIVE_EXECUTION in self.options:
    #       l.debug("Switching to native.")
    #       self.native_env = self.to_native()
    #       return True
    #   elif self.native_env is not None and o.NATIVE_EXECUTION not in self.options:
    #       l.debug("Switching from native.")
    #       self.from_native(self.native_env)
    #       self.native_env = None
    #       return False

    #def set_native(self, n):
    #   if n:
    #       self.options.add(o.NATIVE_EXECUTION)
    #   else:
    #       self.options.remove(o.NATIVE_EXECUTION)
    #   return self.is_native()

    #def to_native(self):
    #   l.debug("Creating native environment.")
    #   m = self.memory.concrete_parts()
    #   r = self.registers.concrete_parts()
    #   size = max(1024*3 * 10, max([0] + m.keys()) + 1024**3)
    #   l.debug("Concrete memory size: %d", size)
    #   return vexecutor.VexEnvironment(self.arch.vex_arch, size, m, r)

    #def from_native(self, e):
    #   for k,v in e.memory.changed_items():
    #       l.debug("Memory: setting 0x%x to 0x%x", k, v)
    #       self.store_mem(k, se.BitVecVal(v, 8))
    #   for k,v in e.registers.changed_items():
    #       l.debug("Memory: setting 0x%x to 0x%x", k, v)
    #       self.store_reg(k, se.BitVecVal(v, 8))

from .s_memory import SimMemory
from .s_arch import Architectures
from .s_errors import SimMergeError, SimValueError
from .s_inspect import BP_AFTER, BP_BEFORE
import simuvex.s_options as o
