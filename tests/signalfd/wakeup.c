#define _GNU_SOURCE
#include <sys/signalfd.h>
#include <sys/epoll.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <poll.h>
#include <signal.h>
#include <unistd.h>
#include <time.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#define REQUIRE(x) do { if (!(x)) { fprintf(stderr,"SFD_SETUP_FAIL line=%d errno=%d\n",__LINE__,errno); exit(2); } } while(0)
static long ms(void) { struct timespec t; REQUIRE(clock_gettime(CLOCK_MONOTONIC,&t)==0); return t.tv_sec*1000L+t.tv_nsec/1000000L; }
int main(int argc,char **argv) {
 setvbuf(stdout,NULL,_IONBF,0); alarm(8); REQUIRE(argc==2);
 int asynchronous=strcmp(argv[1],"pending")!=0;
 int use_epoll=strstr(argv[1],"epoll")!=NULL;
 int thread_directed=strstr(argv[1],"thread")!=NULL;
 sigset_t blocked,selected,pending; sigemptyset(&blocked); sigaddset(&blocked,SIGTERM); sigaddset(&blocked,SIGCHLD); REQUIRE(sigprocmask(SIG_BLOCK,&blocked,NULL)==0);
 sigemptyset(&selected); sigaddset(&selected,SIGTERM);
 int fd=signalfd(-1,&selected,SFD_NONBLOCK|SFD_CLOEXEC); REQUIRE(fd>=0);
 int ep=-1; if(use_epoll) { ep=epoll_create1(EPOLL_CLOEXEC); REQUIRE(ep>=0); struct epoll_event e={.events=EPOLLIN,.data.u64=0x51fd}; REQUIRE(epoll_ctl(ep,EPOLL_CTL_ADD,fd,&e)==0); }
 int go[2],ack[2]; pid_t child=-1; char byte='x';
 if(asynchronous) {
  REQUIRE(pipe(go)==0 && pipe(ack)==0); pid_t parent=getpid(); child=fork(); REQUIRE(child>=0);
  if(!child) {
   close(go[1]); close(ack[1]); REQUIRE(read(go[0],&byte,1)==1);
   struct timespec delay={.tv_sec=0,.tv_nsec=200000000}; REQUIRE(nanosleep(&delay,NULL)==0);
   int rc=thread_directed ? syscall(SYS_tgkill,parent,parent,SIGTERM) : kill(parent,SIGTERM); REQUIRE(rc==0);
   REQUIRE(read(ack[0],&byte,1)==1); _exit(0);
  }
  close(go[0]); close(ack[0]);
 } else REQUIRE(kill(getpid(),SIGTERM)==0);
 printf("SFD_WAIT_BEGIN mode=%s pid=%d\n",argv[1],getpid());
 long start=ms(); if(asynchronous) REQUIRE(write(go[1],&byte,1)==1);
 int rc,events; errno=0;
 if(use_epoll) { struct epoll_event e={0}; rc=epoll_wait(ep,&e,1,2000); events=e.events; }
 else { struct pollfd p={.fd=fd,.events=POLLIN}; rc=poll(&p,1,asynchronous?2000:0); events=p.revents; }
 int wait_errno=errno; long elapsed=ms()-start;
 REQUIRE(sigpending(&pending)==0); int is_pending=sigismember(&pending,SIGTERM);
 struct signalfd_siginfo info={0}; errno=0; ssize_t bytes=read(fd,&info,sizeof(info)); int read_errno=errno;
 int ready=rc==1 && (events&POLLIN); int timely=!asynchronous || elapsed<1500;
 int good=ready&&timely&&bytes==(ssize_t)sizeof(info)&&info.ssi_signo==SIGTERM;
 printf("SFD_RESULT mode=%s wait_rc=%d events=%#x wait_errno=%d elapsed_ms=%ld pending=%d read=%zd read_errno=%d signo=%u sender_pid=%u ready=%d timely=%d result=%s\n",argv[1],rc,events,wait_errno,elapsed,is_pending,bytes,read_errno,info.ssi_signo,info.ssi_pid,ready,timely,good?"PASS":"FAIL");
 if(asynchronous) { REQUIRE(write(ack[1],&byte,1)==1); int status; REQUIRE(waitpid(child,&status,0)==child); REQUIRE(WIFEXITED(status)&&WEXITSTATUS(status)==0); close(go[1]); close(ack[1]); }
 if(ep>=0) close(ep);
 close(fd);
 return good?0:1;
}
