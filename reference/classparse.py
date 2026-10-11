"""Minimal JVM .class file parser: constant pool + bytecode disassembly,
just enough to trace literal-constant arguments passed to setter calls
in a given method (e.g. <init>). No JDK required.
"""
import struct
import sys

CONSTANT_TAGS = {
    1: "Utf8", 3: "Integer", 4: "Float", 5: "Long", 6: "Double",
    7: "Class", 8: "String", 9: "Fieldref", 10: "Methodref",
    11: "InterfaceMethodref", 12: "NameAndType", 15: "MethodHandle",
    16: "MethodType", 18: "InvokeDynamic", 19: "Module", 20: "Package",
}

class ClassFile:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.cp = {}  # index -> (tag, value...)
        self._parse()

    def u1(self):
        v = self.data[self.pos]; self.pos += 1; return v
    def u2(self):
        v = struct.unpack_from(">H", self.data, self.pos)[0]; self.pos += 2; return v
    def u4(self):
        v = struct.unpack_from(">I", self.data, self.pos)[0]; self.pos += 4; return v
    def bytes_(self, n):
        v = self.data[self.pos:self.pos+n]; self.pos += n; return v

    def _parse(self):
        magic = self.u4()
        assert magic == 0xCAFEBABE, "not a class file"
        self.minor = self.u2(); self.major = self.u2()
        cp_count = self.u2()
        i = 1
        while i < cp_count:
            tag = self.u1()
            if tag == 1:  # Utf8
                length = self.u2()
                raw = self.bytes_(length)
                try:
                    s = raw.decode("utf-8")
                except UnicodeDecodeError:
                    s = raw.decode("latin1")
                self.cp[i] = (tag, s)
            elif tag in (7, 8, 16, 19, 20):  # Class, String, MethodType, Module, Package
                self.cp[i] = (tag, self.u2())
            elif tag in (9, 10, 11, 12):  # Fieldref, Methodref, IfaceMethodref, NameAndType
                self.cp[i] = (tag, self.u2(), self.u2())
            elif tag == 3:  # Integer
                self.cp[i] = (tag, struct.unpack_from(">i", self.data, self.pos)[0]); self.pos += 4
            elif tag == 4:  # Float
                self.cp[i] = (tag, struct.unpack_from(">f", self.data, self.pos)[0]); self.pos += 4
            elif tag == 5:  # Long
                self.cp[i] = (tag, struct.unpack_from(">q", self.data, self.pos)[0]); self.pos += 8
                i += 1  # longs/doubles take 2 slots
            elif tag == 6:  # Double
                self.cp[i] = (tag, struct.unpack_from(">d", self.data, self.pos)[0]); self.pos += 8
                i += 1
            elif tag == 15:  # MethodHandle
                self.cp[i] = (tag, self.u1(), self.u2())
            elif tag == 18:  # InvokeDynamic
                self.cp[i] = (tag, self.u2(), self.u2())
            else:
                raise ValueError(f"unknown cp tag {tag} at index {i}")
            i += 1

        self.access_flags = self.u2()
        self.this_class = self.u2()
        self.super_class = self.u2()
        iface_count = self.u2()
        self.interfaces = [self.u2() for _ in range(iface_count)]

        self.fields = self._parse_members()
        self.methods = self._parse_members()
        # attributes at class level (ignore)

    def _parse_members(self):
        count = self.u2()
        members = []
        for _ in range(count):
            access = self.u2()
            name_idx = self.u2()
            desc_idx = self.u2()
            attrs = self._parse_attributes()
            members.append({
                "access": access,
                "name": self.utf8(name_idx),
                "desc": self.utf8(desc_idx),
                "attributes": attrs,
            })
        return members

    def _parse_attributes(self):
        count = self.u2()
        attrs = {}
        for _ in range(count):
            name_idx = self.u2()
            length = self.u4()
            raw = self.bytes_(length)
            attrs[self.utf8(name_idx)] = raw
        return attrs

    def utf8(self, idx):
        e = self.cp.get(idx)
        if e and e[0] == 1:
            return e[1]
        return None

    def cp_str(self, idx):
        """Resolve a CONSTANT_String or Utf8 index to its text."""
        e = self.cp.get(idx)
        if not e:
            return None
        if e[0] == 8:  # String -> Utf8 idx
            return self.utf8(e[1])
        if e[0] == 1:
            return e[1]
        return None

    def method_ref_name(self, idx):
        """Resolve a Methodref constant to 'ClassName.methodName:desc'."""
        e = self.cp.get(idx)
        if not e or e[0] not in (10, 11):
            return None
        class_idx, nt_idx = e[1], e[2]
        cls = self.cp.get(class_idx)
        cls_name = self.utf8(cls[1]) if cls else "?"
        nt = self.cp.get(nt_idx)
        if nt:
            mname = self.utf8(nt[1])
            mdesc = self.utf8(nt[2])
            return f"{cls_name}.{mname}:{mdesc}"
        return None

    def field_ref_name(self, idx):
        e = self.cp.get(idx)
        if not e or e[0] != 9:
            return None
        class_idx, nt_idx = e[1], e[2]
        cls = self.cp.get(class_idx)
        cls_name = self.utf8(cls[1]) if cls else "?"
        nt = self.cp.get(nt_idx)
        if nt:
            return f"{cls_name}.{self.utf8(nt[1])}"
        return None

    def find_method(self, name):
        return [m for m in self.methods if m["name"] == name]

    def all_utf8(self):
        return [e[1] for e in self.cp.values() if e[0] == 1]


def disassemble_setters(cf, method_name="<init>"):
    """Walk bytecode of `method_name`, tracking a tiny value-stack, and
    report every invokevirtual/invokespecial call whose *last pushed
    literal* (int/long/double/string) we can identify -- i.e. simple
    'push constant; call setter(...)' patterns."""
    methods = cf.find_method(method_name)
    results = []
    for m in methods:
        code_attr = m["attributes"].get("Code")
        if not code_attr:
            continue
        # Code attribute layout: max_stack(2) max_locals(2) code_length(4) code[...]
        max_stack, max_locals, code_length = struct.unpack_from(">HHI", code_attr, 0)
        code = code_attr[8:8+code_length]
        results.append((m["desc"], _walk(cf, code)))
    return results


def _walk(cf, code):
    events = []
    last_const = None  # (kind, value) of most recently pushed literal
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        start = i
        i += 1
        if op == 0x10:  # bipush
            val = struct.unpack_from(">b", code, i)[0]; i += 1
            last_const = ("int", val)
        elif op == 0x11:  # sipush
            val = struct.unpack_from(">h", code, i)[0]; i += 2
            last_const = ("int", val)
        elif op == 0x12:  # ldc
            idx = code[i]; i += 1
            last_const = _resolve_ldc(cf, idx)
        elif op == 0x13:  # ldc_w
            idx = struct.unpack_from(">H", code, i)[0]; i += 2
            last_const = _resolve_ldc(cf, idx)
        elif op == 0x14:  # ldc2_w (long/double)
            idx = struct.unpack_from(">H", code, i)[0]; i += 2
            last_const = _resolve_ldc(cf, idx)
        elif 0x02 <= op <= 0x08:  # iconst_m1 .. iconst_5
            last_const = ("int", op - 0x03)
        elif op in (0x09, 0x0a):  # lconst_0/1
            last_const = ("long", op - 0x09)
        elif op in (0x0b, 0x0c, 0x0d):  # fconst_0/1/2
            last_const = ("float", op - 0x0b)
        elif op in (0x0e, 0x0f):  # dconst_0/1
            last_const = ("double", op - 0x0e)
        elif op == 0xb6 or op == 0xb7:  # invokevirtual / invokespecial
            idx = struct.unpack_from(">H", code, i)[0]; i += 2
            mname = cf.method_ref_name(idx)
            events.append(("CALL", mname, last_const))
            last_const = None
        elif op == 0xb8:  # invokestatic
            idx = struct.unpack_from(">H", code, i)[0]; i += 2
            mname = cf.method_ref_name(idx)
            events.append(("CALLSTATIC", mname, last_const))
            last_const = None
        elif op == 0xb5:  # putfield
            idx = struct.unpack_from(">H", code, i)[0]; i += 2
            fname = cf.field_ref_name(idx)
            events.append(("PUTFIELD", fname, last_const))
            last_const = None
        else:
            # skip operands for common fixed/variable-length ops we don't care about
            i += _operand_len(op, code, i)
    return events


def _resolve_ldc(cf, idx):
    e = cf.cp.get(idx)
    if not e:
        return ("?", None)
    tag = e[0]
    if tag == 8:  # String
        return ("string", cf.utf8(e[1]))
    if tag == 3:
        return ("int", e[1])
    if tag == 4:
        return ("float", e[1])
    if tag == 5:
        return ("long", e[1])
    if tag == 6:
        return ("double", e[1])
    return ("?", e)


# Fixed operand-length table (bytes AFTER the opcode byte) for every
# standard JVM opcode we might walk over without special handling.
# tableswitch/lookupswitch/wide are variable-length and handled specially.
_FIXED_LEN = {
    0x10:1,0x11:2,0x12:1,0x13:2,0x14:2,  # bipush,sipush,ldc,ldc_w,ldc2_w (handled above but harmless here)
    0x15:1,0x16:1,0x17:1,0x18:1,0x19:1,  # iload,lload,fload,dload,aload
    0x36:1,0x37:1,0x38:1,0x39:1,0x3a:1,  # istore,lstore,fstore,dstore,astore
    0xa9:1,                              # ret
    0xbc:1,                              # newarray
    0xbb:2,0xbd:2,0xc0:2,0xc1:2,         # new, anewarray, checkcast, instanceof
    0xb2:2,0xb3:2,0xb4:2,0xb5:2,0xb6:2,0xb7:2,0xb8:2,  # getstatic,putstatic,getfield,putfield,invokevirtual,invokespecial,invokestatic
    0xb9:4,                              # invokeinterface
    0xba:4,                              # invokedynamic
    0xc5:3,                              # multianewarray
    0x84:2,                              # iinc
    0xa7:2,0xa8:2,                       # goto, jsr
    0xc8:4,0xc9:4,                       # goto_w, jsr_w
    0x99:2,0x9a:2,0x9b:2,0x9c:2,0x9d:2,0x9e:2,0x9f:2,
    0xa0:2,0xa1:2,0xa2:2,0xa3:2,0xa4:2,0xa5:2,0xa6:2,  # if_* / ifnull / ifnonnull family
    0xc6:2,0xc7:2,                       # ifnull, ifnonnull
    0xc4:0,                              # wide (handled specially below if needed)
}
def _operand_len(op, code, i):
    if op in (0xaa, 0xab):  # tableswitch / lookupswitch: 0-3 pad bytes then table
        # i currently points just past opcode; compute padding to 4-byte boundary
        # relative to the START of the instruction stream (code array index 0).
        pad = (-i) % 4
        pos = i + pad
        if op == 0xaa:  # tableswitch: default(4) low(4) high(4) then (high-low+1) offsets(4 each)
            default_, low, high = struct.unpack_from(">iii", code, pos)
            pos += 12
            n = high - low + 1
            pos += 4 * n
        else:  # lookupswitch: default(4) npairs(4) then npairs*(match,offset)(4,4)
            default_, npairs = struct.unpack_from(">ii", code, pos)
            pos += 8
            pos += 8 * npairs
        return pos - i
    if op in _FIXED_LEN:
        return _FIXED_LEN[op]
    return 0


if __name__ == "__main__":
    path = sys.argv[1]
    method = sys.argv[2] if len(sys.argv) > 2 else "<init>"
    with open(path, "rb") as f:
        cf = ClassFile(f.read())
    print(f"=== {path} :: constant pool Utf8 strings ===")
    for s in cf.all_utf8():
        if s and not s.startswith("(") and "/" not in s and len(s) < 60:
            pass
    print("\n".join(sorted(set(cf.all_utf8()))))
    show_all = "--all" in sys.argv
    print(f"\n=== disassembly of {method}, call trace ===")
    for desc, events in disassemble_setters(cf, method):
        print(f"-- method desc {desc} --")
        for kind, name, const in events:
            if show_all or (name and ("set" in (name.split(".")[1].split(":")[0] if "." in name else "") or kind=="PUTFIELD")):
                print(f"  {kind:10s} {name!s:55s} <- {const}")
