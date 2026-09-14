import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion.live_messages import LiveMessageStore
from companion.capture_clock import CaptureClock
class TimingTests(unittest.TestCase):
 def test_local_receipt_ignores_sender_time(self):
  clock=CaptureClock();clock.update(pdt_epoch=1000,completed_private_media_seconds=100,target_duration=1,monotonic_time=20)
  store=LiveMessageStore(clock=clock)
  for i,sent in enumerate([1990,1993,1998]):
   store.add(platform='youtube',source_id=str(i),author={},text='x',received_at=2000,received_monotonic=20,platform_sent_at=sent)
  self.assertEqual([m['mediaTime'] for m in store.query(after_seq=0)],[1100,1100,1100])
 def test_missing_or_invalid_time_falls_back_and_explicit_time_is_preserved(self):
  clock=CaptureClock();clock.update(pdt_epoch=1000,completed_private_media_seconds=100,target_duration=1,monotonic_time=20)
  store=LiveMessageStore(clock=clock)
  for i,sent in enumerate([None,2001,float('nan'),1900]):
   m=store.add(platform='twitch',source_id=str(i),author={},text='x',received_at=2000,received_monotonic=20,platform_sent_at=sent)
   self.assertEqual(m.media_time,1100)
  m=store.add(platform='youtube',source_id='explicit',author={},text='x',received_at=2000,received_monotonic=20,platform_sent_at=1990,media_time=1099)
  self.assertEqual(m.media_time,1099)
 def test_pending_clock_keeps_local_receive_intervals(self):
  clock=CaptureClock();store=LiveMessageStore(clock=clock)
  for i,sent in enumerate([1990,1998]):
   store.add(platform='youtube',source_id=str(i),author={},text='x',received_at=2000,received_monotonic=18+i*2,platform_sent_at=sent)
  clock.update(pdt_epoch=1000,completed_private_media_seconds=100,target_duration=1,monotonic_time=20)
  store.flush_pending(monotonic_time=20)
  self.assertEqual([m['mediaTime'] for m in store.query(after_seq=0)],[1098,1100])
if __name__=='__main__':unittest.main()
