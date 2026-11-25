import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from evidence import records,clean

class EvidenceTests(unittest.TestCase):
    def test_timings_and_all_actions_retained_survey_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'trace.sqlite'
            with sqlite3.connect(p) as c:
                c.execute('CREATE TABLE events(id INTEGER,session_id TEXT,kind TEXT,received_at REAL,elapsed_ms REAL,payload_json TEXT)')
                for i,kind,payload in [(1,'pointermove',{'x':42,'duration_ms':19}),(2,'click',{'target':'Next','prolific_pid':'hidden','nested':{'answers_json':{'secret':9},'time_ms':12}}),(3,'study_screen',{'phase':'survey','visible_text':'private questionnaire'}),(4,'input',{'value':'SECRET ANSWER'})]:
                    c.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(i,'actor',kind,i,12,json.dumps(payload)))
                c.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(5,'other','click',5,15,'{}'))
            rows=records('webarena',p,'actor')['events']
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['payload_json']['x'],42)
            self.assertEqual(rows[0]['payload_json']['duration_ms'],19)
            self.assertNotIn('hidden',json.dumps(rows));self.assertNotIn('SECRET',json.dumps(rows));self.assertNotIn('answers_json',json.dumps(rows))
            self.assertEqual(rows[1]['payload_json']['nested']['time_ms'],12)

    def test_zero_and_null_not_imputed(self):
        self.assertEqual(clean({'response_time_ms':0,'answered_at':None,'prolific_pid':'x'}),{'response_time_ms':0,'answered_at':None})

if __name__=='__main__':unittest.main()
