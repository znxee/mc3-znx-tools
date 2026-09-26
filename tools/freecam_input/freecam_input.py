#!/usr/bin/env python3
"""Keyboard/mouse input bridge for freecam.mod, modderG 2026-09-05 (GPL-3.0).

Only sends controls while the selected PCSX2 process is foreground. No DLL
injection, process-memory scanning, global hooks, or keyboard recording.
PINE references: https://github.com/PCSX2/pcsx2/blob/master/pcsx2/PINE.h
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getasynckeystate
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
import socket
import struct
import time

MAGIC=0x4D433346
SITE=0x001A157C
KEYS=[(ord('P'),0),(ord('W'),1),(ord('S'),2),(ord('A'),3),(ord('D'),4),
      (ord('R'),5),(ord('F'),6),(0x10,7),(0x11,8),(ord('Q'),9),(ord('E'),10),
      (ord('O'),11),(ord('Z'),12),(ord('X'),13),(ord('M'),14),(ord('N'),15),
      (ord('T'),16),(0x25,17),(0x27,18),(0x26,19),(0x28,20),(ord('H'),21)]

class PineError(RuntimeError): pass

class Pine:
    def __init__(self,port=28011,timeout=1.0):
        self.sock=socket.create_connection(('127.0.0.1',port),timeout)
        self.sock.settimeout(timeout)
    def close(self): self.sock.close()
    def exact(self,n):
        out=bytearray()
        while len(out)<n:
            chunk=self.sock.recv(n-len(out))
            if not chunk: raise ConnectionError('PCSX2 closed PINE')
            out.extend(chunk)
        return bytes(out)
    def request(self,body,expected):
        self.sock.sendall(struct.pack('<I',len(body)+4)+body)
        size=struct.unpack('<I',self.exact(4))[0]
        if not 5<=size<=1024*1024: raise PineError('invalid PINE size')
        reply=self.exact(size-4)
        if reply[0]: raise PineError('PINE IPC_FAIL: game not available yet')
        if len(reply)!=expected+1: raise PineError('unexpected PINE reply')
        return reply[1:]
    def read(self,addresses):
        body=b''.join(b'\x02'+struct.pack('<I',a) for a in addresses)
        return struct.unpack('<%dI'%len(addresses),self.request(body,4*len(addresses)))
    def write(self,pairs):
        body=b''.join(b'\x06'+struct.pack('<II',a,v&0xFFFFFFFF) for a,v in pairs)
        self.request(body,0)

def valid_ptr(a,size=4): return 0x100000<=a<=0x2000000-size and not a&3

class Freecam:
    def __init__(self,pine):
        self.pine=pine
        self.address,self.gate=self.discover()
        seq,x,y,beat=self.pine.read([self.address+i for i in (32,40,44,52)])
        self.seq=seq&~1; self.x=x; self.y=y; self.beat=beat
    def discover(self):
        ins=self.pine.read([SITE])[0]
        if ins==0x0C1455E4 or ins>>26!=3:
            raise PineError('freecam.mod not installed: start mc3boot with freecam.mod = defer')
        gate=(ins&0x3FFFFFF)<<2
        if not valid_ptr(gate,24): raise PineError('invalid camera target')
        magic,addr,version,size=self.pine.read([gate+i for i in (8,12,16,20)])
        if magic!=MAGIC or version!=1 or size!=64 or not valid_ptr(addr,216):
            raise PineError('the camera hook does not belong to freecam.mod v1')
        hdr=self.pine.read([addr+i for i in (0,4,8,12)])
        if hdr!=(MAGIC,1,216,1): raise PineError('incompatible freecam state: '+str(hdr))
        return addr,gate
    def validate(self):
        if self.discover()!=(self.address,self.gate):
            raise PineError('game/module reloaded; restart the helper')
    def status(self):
        v=self.pine.read([self.address+i for i in (12,16,20,24,28,204,208)])
        return dict(address=hex(self.address),status=v[0],active=bool(v[1]),
                    fov=struct.unpack('<f',struct.pack('<I',v[2]))[0],
                    camera=hex(v[3]),frame=v[4],live_camera=hex(v[5]),live_frame=v[6])
    def send(self,keys=0,dx=0,dy=0,focused=True):
        self.seq=(self.seq+2)&0xFFFFFFFE
        self.beat=(self.beat+1)&0xFFFFFFFF
        self.x=(self.x+int(dx))&0xFFFFFFFF; self.y=(self.y+int(dy))&0xFFFFFFFF
        a=self.address
        # Cumulative mouse counters survive dropped render frames. Odd/even
        # sequence protects readers from interrupted updates; heartbeat expires
        # after 0.5 emulated seconds if the helper exits or loses the connection.
        self.pine.write([(a+32,self.seq|1),(a+36,keys),(a+40,self.x),(a+44,self.y),
                         (a+48,int(focused)),(a+52,self.beat),(a+32,self.seq)])

class WindowsInput:
    def __init__(self,pid=None):
        if os.name!='nt': raise RuntimeError('this mouse helper requires Windows')
        self.u=C.WinDLL('user32',use_last_error=True)
        self.k=C.WinDLL('kernel32',use_last_error=True)
        self.u.GetForegroundWindow.restype=W.HWND
        self.u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
        self.u.GetAsyncKeyState.argtypes=[C.c_int]; self.u.GetAsyncKeyState.restype=C.c_short
        self.u.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
        self.u.ClientToScreen.argtypes=[W.HWND,C.POINTER(W.POINT)]
        self.u.GetCursorPos.argtypes=[C.POINTER(W.POINT)]
        self.u.SetCursorPos.argtypes=[C.c_int,C.c_int]
        self.k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; self.k.OpenProcess.restype=W.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
        self.k.CloseHandle.argtypes=[W.HANDLE]
        # Cursor and client coordinates must use the same DPI coordinate space.
        self.u.SetProcessDPIAware()
        self.pid=pid; self.capture=True; self.last_window=None; self.l_down=False
    def down(self,key): return bool(self.u.GetAsyncKeyState(key)&0x8000)
    def focused(self):
        hwnd=self.u.GetForegroundWindow()
        if not hwnd: return None
        pid=W.DWORD(); self.u.GetWindowThreadProcessId(hwnd,C.byref(pid))
        if self.pid is not None: return hwnd if pid.value==self.pid else None
        handle=self.k.OpenProcess(0x1000,False,pid.value)
        if not handle: return None
        try:
            buf=C.create_unicode_buffer(32768); length=W.DWORD(len(buf))
            if not self.k.QueryFullProcessImageNameW(handle,0,buf,C.byref(length)): return None
            name=os.path.basename(buf.value).lower()
            return hwnd if name.startswith('pcsx2') and name.endswith('.exe') else None
        finally: self.k.CloseHandle(handle)
    def sample(self,active):
        hwnd=self.focused()
        if not hwnd:
            self.last_window=None; self.l_down=False
            return 0,0,0,False,False
        l=self.down(ord('L'))
        if l and not self.l_down: self.capture=not self.capture; self.last_window=None
        self.l_down=l
        keys=sum(1<<bit for vk,bit in KEYS if self.down(vk))
        stop=self.down(0x23) # End: stop bridge, native keyboard stays available.
        if not active or not self.capture:
            self.last_window=None
            return keys,0,0,True,stop
        rect=W.RECT(); center=W.POINT(); mouse=W.POINT()
        if not self.u.GetClientRect(hwnd,C.byref(rect)): return keys,0,0,True,stop
        center.x=(rect.right-rect.left)//2; center.y=(rect.bottom-rect.top)//2
        if not self.u.ClientToScreen(hwnd,C.byref(center)): return keys,0,0,True,stop
        if not self.u.GetCursorPos(C.byref(mouse)): return keys,0,0,True,stop
        dx=mouse.x-center.x if self.last_window==hwnd else 0
        dy=mouse.y-center.y if self.last_window==hwnd else 0
        self.u.SetCursorPos(center.x,center.y)
        self.last_window=hwnd
        return keys,dx,dy,True,stop

def main():
    ap=argparse.ArgumentParser(description='mouse and keyboard for freecam.mod through local PINE')
    ap.add_argument('--port',type=int,default=28011)
    ap.add_argument('--pid',type=int,help='PCSX2 PID, if the executable was renamed')
    ap.add_argument('--status',action='store_true',help='only check whether the module is loaded')
    args=ap.parse_args(); pine=None; pck_path=None
    try:
        pine=Pine(args.port)
        pck_path=Freecam(pine)
        if args.status:
            print(json.dumps(pck_path.status(),indent=2)); return 0
        inputs=WindowsInput(args.pid)
        print('Freecam connected. Focus PCSX2. P: on/off | L: release mouse | End: quit.',flush=True)
        validate_at=time.monotonic()+1
        while True:
            start=time.monotonic()
            if start>=validate_at:
                pck_path.validate(); validate_at=start+1
            status=pck_path.status()
            keys,dx,dy,focus,stop=inputs.sample(status['active'])
            if stop: break
            pck_path.send(keys,dx,dy,focus)
            time.sleep(max(0,1/120-(time.monotonic()-start)))
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError,RuntimeError) as exc:
        print('Freecam: '+str(exc),flush=True)
        print('PINE must be enabled and free. Old PINE/ReShade readers can take the only connection; stop the competing reader and try again.',flush=True)
        return 1
    finally:
        if pck_path is not None and not args.status:
            try:
                pck_path.validate(); pck_path.send(focused=False)
            except (OSError,RuntimeError): pass
        if pine is not None: pine.close()

if __name__=='__main__': raise SystemExit(main())
