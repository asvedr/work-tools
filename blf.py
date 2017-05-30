import struct
import zlib
import datetime
import time
from functools import reduce
import sys
import argparse

# 0 = unknown, 2 = CANoe
APPLICATION_ID = 5

# Header must be 144 bytes in total
# signature ("LOGG"), header size,
# application ID, application major, application minor, application build,
# bin log major, bin log minor, bin log build, bin log patch,
# file size, uncompressed size, count of objects, count of objects read,
# time start (SYSTEMTIME), time stop (SYSTEMTIME)
FILE_HEADER_STRUCT = struct.Struct("<4sLBBBBBBBBQQLL8H8H72x")

# signature ("LOBJ"), header size, header version (1), object size, object type,
# flags, object version, size uncompressed or timestamp
OBJ_HEADER_STRUCT = struct.Struct("<4sHHLLL2xHQ")

# channel, flags, dlc, arbitration id, data
CAN_MSG_STRUCT = struct.Struct("<HBBL8s")

# channel, length
CAN_ERROR_STRUCT = struct.Struct("<HH4x")

# commented event type, foreground color, background color, relocatable,
# group name length, marker name length, description length
GLOBAL_MARKER_STRUCT = struct.Struct("<LLL3xBLLL12x")


CAN_MESSAGE = 1
CAN_ERROR = 2
LOG_CONTAINER = 10
GLOBAL_MARKER = 96

CAN_MSG_EXT = 0x80000000
REMOTE_FLAG = 0x80


def timestamp_to_systemtime(timestamp):
    if timestamp is None or timestamp < 631152000:
        # Probably not a Unix timestamp
        return (0, 0, 0, 0, 0, 0, 0, 0)
    t = datetime.datetime.fromtimestamp(timestamp)
    return (t.year, t.month, t.isoweekday() % 7, t.day,
            t.hour, t.minute, t.second, int(round(t.microsecond / 1000.0)))


def systemtime_to_timestamp(systemtime):
    try:
        t = datetime.datetime(
            systemtime[0], systemtime[1], systemtime[3],
            systemtime[4], systemtime[5], systemtime[6], systemtime[7] * 1000)
        return time.mktime(t.timetuple()) + systemtime[7] / 1000.0
    except ValueError:
        return 0

class Message(object):
    def __init__(self, **args):
        self.timestamp       = args['timestamp']
        self.arbitration_id  = args.get('arbitration_id', 0)
        self.is_extended_id  = args.get('extended_id', False)
        self.is_remote_frame = args.get('is_remote_frame', False)
        self.dlc             = args.get('dlc', 0)
        self.data            = args.get('data', [])
        self.is_error_frame  = args.get('is_error_frame', False)

class BLFReader(object):
    """
    Iterator of CAN messages from a Binary Logging File.

    Only CAN messages and error frames are supported. Other object types are
    silently ignored.
    """

    def __init__(self, filename):
        self.fp = open(filename, "rb")
        data = self.fp.read(FILE_HEADER_STRUCT.size)
        header = FILE_HEADER_STRUCT.unpack(data)
        #print(header)
        assert header[0] == b"LOGG", "Unknown file format"
        self.start_timestamp = systemtime_to_timestamp(header[14:22])

    def __iter__(self):
        tail = b""
        while True:
            data = self.fp.read(OBJ_HEADER_STRUCT.size)
            if not data:
                # EOF
                break
            header = OBJ_HEADER_STRUCT.unpack(data)
            #print(header)
            assert header[0] == b"LOBJ", "Parse error"
            obj_type = header[4]
            obj_data_size = header[3] - OBJ_HEADER_STRUCT.size
            obj_data = self.fp.read(obj_data_size)
            # Read padding bytes
            self.fp.read(obj_data_size % 4)
            if obj_type == LOG_CONTAINER:
                uncompressed_size = header[7]
                data = zlib.decompress(obj_data, 15, uncompressed_size)
                if tail:
                    data = tail + data
                pos = 0
                while pos + OBJ_HEADER_STRUCT.size < len(data):
                    header = OBJ_HEADER_STRUCT.unpack(
                        data[pos:pos + OBJ_HEADER_STRUCT.size])
                    #print(header)
                    assert header[0] == b"LOBJ", "Parse error"
                    obj_size = header[3]
                    if pos + obj_size > len(data):
                        # Object continues in next log container
                        break
                    obj_data = data[pos + OBJ_HEADER_STRUCT.size:pos + obj_size]
                    obj_type = header[4]
                    timestamp = header[7] / 1000000000.0 + self.start_timestamp
                    if obj_type == CAN_MESSAGE:
                        (channel, flags, dlc, can_id,
                         can_data) = CAN_MSG_STRUCT.unpack(obj_data)
                        msg = Message(timestamp=timestamp,
                                      arbitration_id=can_id & 0x1FFFFFFF,
                                      extended_id=bool(can_id & CAN_MSG_EXT),
                                      is_remote_frame=bool(flags & REMOTE_FLAG),
                                      dlc=dlc,
                                      data=can_data[:dlc])
                        msg.channel = channel
                        yield msg
                    elif obj_type == CAN_ERROR:
                        channel, length = CAN_ERROR_STRUCT.unpack(obj_data)
                        msg = Message(timestamp=timestamp, is_error_frame=True)
                        msg.channel = channel
                        yield msg
                    pos += obj_size
                    # Add padding bytes
                    pos += obj_size % 4
                # Save remaing data that could not be processed
                tail = data[pos:]
        self.fp.close()


def int2s(cnt):
    def foo(val):
        val = hex(val)[2:]
        if len(val) < cnt:
            for _ in range(cnt - len(val)):
                val = '0' + val
        return val
    return foo

head = ['timestamp', 'is_remote_frame', 'extended_id', 'is_error_frame', 'dlc', 'arbitration_id', 'data']
head = reduce(lambda a,b: '%s\t%s' % (a,b), head)

def to8(mess):
    mess = list(mess)[:]
    if len(mess) < 8:
        for _ in range(8 - len(mess)):
            mess.append(0)
    return mess

def mess2s(mess):
    numsshow = int2s(3)(mess.arbitration_id) + ' ' + reduce(lambda a,b: a + ' ' + b, map(int2s(2), to8(mess.data)))
    vals = [mess.timestamp, mess.is_remote_frame, mess.is_extended_id, mess.is_error_frame, mess.dlc, numsshow]
    return reduce(lambda a,b: '%s\t%s' % (a, b), vals)

def totxt(ipath,opath):
    reader = BLFReader(ipath)
    with open(opath, 'wt') as header:
        header.write(head)
        header.write('\n')
        for mess in reader:
            header.write(mess2s(mess))
            header.write('\n')

parser = argparse.ArgumentParser(description='convert .blf to .txt')
parser.add_argument('blf', help='binary blf file, input')
parser.add_argument('txt', help='output')

args = vars(parser.parse_args())

totxt(args['blf'], args['txt'])