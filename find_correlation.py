# -*- coding: utf-8 -*-
import sys
import re
import argparse
import os
from argparse import RawTextHelpFormatter
import xml.etree.ElementTree as ET # for reading API.xml

# One process-thread-message filter
class Filter(object):
	# make new filter or return none if string empty
	@classmethod
	def make(cls, param):
		param = param.strip()
		if len(param) == 0:
			return None
		else:
			# parse conf as dict. add brackets and replace keywords to env
			try:
				env     = {'proc': 'proc', 'thread': 'thread', 'message': 'message', 'func': 'func'}
				try:
					conf = eval(param, env)
				except:
					conf = eval('{%s}' % param, env)
				proc    = re.compile(conf['proc']) if 'proc' in conf else None
				thread  = re.compile(conf['thread']) if 'thread' in conf else None
				message = re.compile(conf['message']) if 'message' in conf else None
				func    = re.compile(conf['func']) if 'func' in conf else None
			except:
				raise Exception('bad filter schema', param)
			return cls(proc, thread, message, func)
	def __init__(self, proc, thread, message, func):
		self.proc_re = proc
		self.thread_re = thread
		self.message_re = message
		self.func_re = func
	def match(self, indexer, logline):
		'''(indexer, logline)
			indexer - schema to get proc, thread and mess from logline
			then matching components
		'''
		try:
			if self.proc_re and not self.proc_re.match(logline[indexer.proc]):
				return False
			if self.thread_re and not self.thread_re.match(logline[indexer.thread]):
				return False
			if self.func_re and not self.func_re.match(logline[indexer.func]):
				return False
			if self.message_re and not self.message_re.match(logline[indexer.message]):
				return False
			return True
		except:
			return False


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
			return cls(9, 5, 6, 7, 9)
		elif float(updcount) / len(lines) > 3.0 / 5:
			return cls(11, 7, 8, 9, 11)
		else:
		# if none - we can't parse this
			raise Exception("can't parse this log")
	def __init__(self, maxsplit, proc, thread, func, message):
		# All that fields is int indexes
		self.proc       = proc
		self.thread     = thread
		self.message    = message
		self.func       = func
		self.maxsplit   = maxsplit

class Mask:
	'''
		Container for rules 
		Howto: init, set 'indexer', use save_this_line
	'''
	def __init__(self, filepath, is_exclude=False):
		try:
			with open(filepath, 'rt') as header:
				self.matchers = list(filter(None, map(Filter.make, header)))
		except FileNotFoundError:
			raise Exception("can't open rule file")
		self.is_exclude = is_exclude
		self.indexer = None
	def save_this_line(self, line):
		indexer = self.indexer
		assert(indexer)
		if self.is_exclude:
			for m in self.matchers:
				if m.match(indexer, line):
					return False
			return True
		else:
			for m in self.matchers:
				if m.match(indexer, line):
					return True
			return False


class CorrelationLooker:
	def look_in(self, events, trigger, depth, look_before=True):
		'''
			(events, trigger, depth, probability)
			events      - list of events
			trigger     - predicate to see reason of what we looking for
			depth       - how much events before accident we looking at
		'''
		self._depth = depth
		predecessors = []
		for i in range(len(events)):
			event = events[i]
			if trigger(event):
				current_predecessors = []
				if look_before:
					for j in range(1, depth + 1):
						if i - j > 0:
							current_predecessors.append(events[i - j])
					predecessors.append(current_predecessors)
				else:
					for j in range(1, depth + 1):
						if i + j < len(events):
							current_predecessors.append(events[i + j])
					current_predecessors.reverse()
					predecessors.append(current_predecessors)
		self._predecessors = predecessors
	def accident_count(self):
		return len(self._predecessors)
	def get_correlation(self, id_comparer, probability):
		'''
			look_in must be called before it
			(id_comparer, probability)
			id_comparer - function to compare identity
			probability - euristic parameter for answer
		'''
		# all_events : [(event, {number_of_accident})]
		all_events = []
		for accident_id in range(len(self._predecessors)):
			for predecessor in self._predecessors[accident_id]:
				found = False
				for pair in all_events:
					if id_comparer(pair[0], predecessor):
						# found match! upd accident counter and break loop
						pair[1].add(accident_id)
						found = True
						break
				if not found:
					all_events.append( (predecessor, set([accident_id])) )
		max_num = float(len(self._predecessors))
		event_prob_list = map(lambda pair: (pair[0], len(pair[1]) / max_num), all_events)
		#print('%s: %s' % (max_num, [e[1] for e in event_prob_list]))
		event_prob_list = filter(lambda pair: pair[1] >= probability, event_prob_list)
		event_prob_list = list(event_prob_list)
		event_prob_list.reverse()
		event_prob_list.sort(key=lambda a: a[1])
		return event_prob_list

def read_lines(path):
	encs = ['utf-8', 'cp1251']
	for enc in encs:
		try:
			with open(path, 'r', encoding=enc) as h:
				return list(h.readlines())
		except Exception as e:
			print(e)
			pass
	raise Exception('bad encoding')

def find_correlation(mask, depth, path, probability, params, look_before):
	lines = read_lines(path)
	indexer = Indexer.try_lines(lines[1:6])
	mask.indexer = indexer
	lines = tuple((line.split('/') for line in lines))
	looker = CorrelationLooker()
	# get precursors of all accidents
	looker.look_in(lines, mask.save_this_line, depth, look_before)
	if looker.accident_count() == 0:
		print('ACCIDENT NOT FOUND')
		return
	if looker.accident_count() == 1:
		print('ACCIDENT ONLY ONE')
		return
	print('ACCIDENT COUNT', looker.accident_count())
	# by full params
	comparers = {
			'p' : lambda a,b: a[indexer.proc] == b[indexer.proc],
			't' : lambda a,b: a[indexer.thread] == b[indexer.thread],
			'f' : lambda a,b: a[indexer.func] == b[indexer.func],
			'm' : lambda a,b: a[indexer.message] == b[indexer.message]
		}
	def compare(funcs, a,b):
		try:
			for f in funcs:
				if not f(a,b):
					return False
			return True
		except:
			return False
	def case(name, funcs):
		funcs = [comparers[name] for name in funcs]
		reason = looker.get_correlation(lambda a,b: compare(funcs, a, b), probability)
		if len(reason) == 0:
			print('NOT FOUND')
		else:
			print(name)
			for ans in reason:
				print('  (%s%%): "%s"' % (int(ans[1] * 100), ('/').join(ans[0]).strip()))
	case('BY %s' % params, params)

parser = argparse.ArgumentParser(description='', formatter_class=RawTextHelpFormatter)
selfdir = os.path.dirname(__file__)
parser.add_argument('-f', help='filter file', default='filter')
parser.add_argument('-d', help='depth for search', default='10')
parser.add_argument('log', help='log file')
parser.add_argument('-p', help='prop', default='80')
parser.add_argument('--params', help='compare params', default='p,t,f,m')
parser.add_argument('--after', help='search in events after accident', action='store_true')
# add argument --frequency
args = vars(parser.parse_args())

mask = Mask(args['f'], is_exclude=False)
try:
	depth = int(args['d'])
	assert depth > 0
except:
	print('bad depth')
	exit()

find_correlation (
		mask,
		depth,
		args['log'],
		float(args['p']) / 100.0,
		args['params'].split(','),
		not args['after']
	)