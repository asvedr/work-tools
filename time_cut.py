import sys
import re
import argparse
import os
from argparse import RawTextHelpFormatter

def msecs(h,m,s,ms):
	return (s + (m * 60) + (h * 60 * 60)) * 1000 + ms

class Indexer(object):
	''' schema to split log line and indexing in it '''
	@classmethod
	def try_lines(cls, lines):
		# default:
		#  0 |  1  |    2    |  3| 4 |    5    |       6   |     7       | 8  | 9 
		# day/month/year+time/PID/TID/proc name/thread name/function name/line/=message 
		# udp:
		#  0 | 1|2                | 3| 4               | 5 | 6 | 7   | 8   | 9 | 10 | 11
		# [my/mm/md mh:mm:ms.n] 04/20/2017 17:15:52.677/pid/tid/pname/tname/fun/line/=message 
		defcount = 0
		updcount = 0
		num_match   = re.compile('\d+')
		brnum_match = re.compile('\[\d+')
		for line in lines:
			split = line.split('/')
			if len(split) < 10:
				continue
			if num_match.match(split[0]) and num_match.match(split[1]) and num_match.match(split[2]):
				defcount += 1
			elif brnum_match.match(split[0]) and num_match.match(split[1]) and num_match.match(split[2]):
				updcount += 1
		# if most of lines is in one template then all conf in this template
		if float(defcount) / len(lines) > 3.0 / 5:
			return cls( 2 )
		elif float(updcount) / len(lines) > 3.0 / 5:
			return cls( 4 )
		else:
		# if none - we can't parse this
			raise Exception("can't parse this log", '%s %s %s' % (defcount, updcount, len(lines)))

	def __init__(self, time):
		# All that fields is int indexes
		self._time = time
		self.maxsplit = 6
		self.ok = 0

	def time(self,line):
		try:
			line = line[self._time]
			a = line.index(' ')
			b = line.index('.')
			t = line[a+1:b].split(':')
			h = int(t[0])
			m = int(t[1])
			s = int(t[2])
			ms = int(line[b+1:])
			return msecs(h, m, s, ms)
		except:
			return None

class Liners(object):
	def __init__(self, path):
		header = None
		encs = ['utf-8', 'cp1251']
		for enc in encs:
			try:
				header = open(path, 'r', encoding=enc)
				lines = header.readlines()
				# ok
				self.header = header
				self.lines = lines
				return
			except:
				if not (header is None):
					header.close()
					header = None
		raise Exception("can't open with %s" % encs)
	def __enter__(self):
		return list(self.lines)
	def __exit__(self, *args):
		return self.header.__exit__(*args)

def remake_log(input_path, output_path, time_from, time_to):
	''' filter contents and replace code to message '''
	output = open(output_path, 'wt', encoding='utf-8') if output_path else sys.stdout

	print(time_from, time_to)

	#only for events from vs_can
	#with open(input_path, 'r', encoding='cp1251') as header:
	before = 0
	used   = 0
	after  = 0
	out    = 0
	with Liners(input_path) as lines:
		#lines = list(header.readlines())
		indexer = Indexer.try_lines(lines[1:16])
		for line in lines:
			log = line.split('/', maxsplit=indexer.maxsplit)
			time = indexer.time(log)
			if time is None:
				out += 1
				continue
			if time < time_from:
				before += 1
				continue
			elif time > time_to:
				after += 1
				continue
			else:
				used += 1
				resline = '/'.join(log)
				output.write(resline)
	print(before, used, after, out, before + used + out + after)

	output.flush()
	# output will automaticly closed correctly

parser = argparse.ArgumentParser(description='', formatter_class=RawTextHelpFormatter)
selfdir = os.path.dirname(__file__)
parser.add_argument('-o', help='out file', default=None)
parser.add_argument('log', help='log file', default=None)
parser.add_argument('-f', help='time from hh:mm:ss', default=None)
parser.add_argument('-t', help='time to hh:mm:ss', default=None)
args = vars(parser.parse_args())
def psecs(s):
	t = s.split(':')
	h = int(t[0])
	m = int(t[1])
	s = t[2].split('.')
	ms = int(s[1]) if len(s) == 2 else 0
	s = int(s[0])
	return msecs(h,m,s,ms)
try:
	time_from = psecs(args['f'])
except:
	print('time from error')
	exit()
try:
	time_to = psecs(args['t'])
except:
	print('time to error')
	exit()
remake_log(args['log'], args['o'], time_from, time_to)
