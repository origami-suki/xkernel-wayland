#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <sys/time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>

static int failures, checks, kind;
static void must(int ok, const char *name) { if (!ok) { perror(name); exit(2); } }
static void check(int ok, const char *name) {
    printf("CREDENTIALS type=%d %s %s\n", kind, name, ok ? "PASS" : "FAIL");
    ++checks; failures += !ok;
}
static void option(int fd, int value) {
    must(setsockopt(fd, SOL_SOCKET, SO_PASSCRED, &value, sizeof(value)) == 0, "setsockopt");
}
static void pair(int fd[2], int enabled) {
    must(socketpair(AF_UNIX, kind | SOCK_NONBLOCK, 0, fd) == 0, "socketpair");
    option(fd[0], enabled);
    struct timeval timeout = {.tv_usec=100000};
    must(setsockopt(fd[0], SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) == 0, "receive timeout");
}
struct received {
    ssize_t n; size_t control; int flags, found, rights, fd;
    struct ucred cred; char bytes[32]; unsigned char raw[12]; size_t rawlen;
};
static struct received receive(int fd, size_t bytes, size_t capacity, int flags) {
    struct received r = {.fd = -1};
    union { struct cmsghdr align; unsigned char bytes[128]; } control = {0};
    struct iovec iov = {.iov_base = r.bytes, .iov_len = bytes};
    struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = capacity};
    r.n = recvmsg(fd, &msg, flags); r.control = msg.msg_controllen; r.flags = msg.msg_flags;
    if (r.n < 0) return r;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
        if (c->cmsg_level != SOL_SOCKET) continue;
        if (c->cmsg_type == SCM_CREDENTIALS) {
            ++r.found; r.rawlen = c->cmsg_len - CMSG_LEN(0);
            must(r.rawlen <= sizeof(r.raw), "credential length");
            memcpy(r.raw, CMSG_DATA(c), r.rawlen);
            if (r.rawlen == sizeof(r.cred)) memcpy(&r.cred, r.raw, sizeof(r.cred));
        } else if (c->cmsg_type == SCM_RIGHTS) {
            for (size_t i = 0; i + sizeof(int) <= c->cmsg_len - CMSG_LEN(0); i += sizeof(int)) {
                int delivered; memcpy(&delivered, CMSG_DATA(c) + i, sizeof(delivered));
                if (r.fd >= 0) close(r.fd);
                r.fd = delivered; ++r.rights;
            }
        }
    }
    return r;
}
static int identity(struct received r, pid_t pid, uid_t uid, gid_t gid) {
    return r.found == 1 && r.rawlen == sizeof(struct ucred) &&
        r.cred.pid == pid && r.cred.uid == uid && r.cred.gid == gid;
}
static void child_send(int fd, int method) {
    ssize_t n;
    if (method == 0) n = send(fd, "ping", 4, 0);
    else if (method == 1) n = write(fd, "ping", 4);
    else { struct iovec iov[2] = {{"pi",2},{"ng",2}}; n = writev(fd,iov,2); }
    _exit(n == 4 ? 0 : 2);
}
static void *thread_send(void *arg) {
    return (void *)(intptr_t)(send(*(int *)arg, "ping", 4, 0) != 4);
}
static pid_t send_child(int fd, int method) {
    pid_t p = fork(); must(p >= 0, "fork");
    if (!p) child_send(fd, method);
    int status; must(waitpid(p, &status, 0) == p && WIFEXITED(status) && !WEXITSTATUS(status), "child");
    return p;
}
static ssize_t explicit_send(int fd, struct ucred cred, size_t size, int right) {
    union { struct cmsghdr align; char bytes[128]; } control = {0};
    struct iovec iov = {.iov_base = "ping", .iov_len = 4};
    struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = CMSG_SPACE(size)};
    struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
    c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_CREDENTIALS; c->cmsg_len = CMSG_LEN(size);
    memcpy(CMSG_DATA(c), &cred, size < sizeof(cred) ? size : sizeof(cred));
    if (right >= 0) {
        msg.msg_controllen += CMSG_SPACE(sizeof(int)); c = CMSG_NXTHDR(&msg,c);
        c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS; c->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(c), &right, sizeof(right));
    }
    return sendmsg(fd, &msg, 0);
}
static void run_type(int type) {
    kind = type; int fd[2]; struct received r;
    struct ucred self = {getpid(), getuid(), getgid()};
    pair(fd, 1);
    int val = 0; socklen_t len = sizeof(val);
    must(getsockopt(fd[0],SOL_SOCKET,SO_PASSCRED,&val,&len)==0,"getsockopt");
    check(val == 1 && len == sizeof(val), "option-enabled");
    for (int method = 0; method < 3; ++method) {
        pid_t p = send_child(fd[1], method); r = receive(fd[0],32,128,0);
        check(r.n == 4 && !memcmp(r.bytes,"ping",4) && !r.flags && identity(r,p,getuid(),getgid()),
              method == 0 ? "child-send" : method == 1 ? "child-write" : "child-writev");
    }
    pthread_t thread; void *result;
    must(pthread_create(&thread,NULL,thread_send,&fd[1])==0,"pthread_create");
    must(pthread_join(thread,&result)==0 && !result,"pthread_join");
    r=receive(fd[0],32,128,0);
    check(r.n==4 && identity(r,getpid(),getuid(),getgid()),"thread-uses-process-pid");
    pid_t p = send_child(fd[1],0);
    for (int i = 0; i < 3; ++i) {
        r = receive(fd[0],32,128,i < 2 ? MSG_PEEK : 0);
        check(r.n == 4 && identity(r,p,getuid(),getgid()),i < 2 ? "peek-preserves" : "consume-after-peek");
    }
    if (kind == SOCK_STREAM) {
        p = send_child(fd[1],0);
        r=receive(fd[0],1,128,0); check(r.n==1 && identity(r,p,getuid(),getgid()),"short-read-first");
        r=receive(fd[0],32,128,0); check(r.n==3 && identity(r,p,getuid(),getgid()),"short-read-rest");
        p=send_child(fd[1],0); pid_t q=send_child(fd[1],0);
        r=receive(fd[0],32,128,0); check(r.n==4 && identity(r,p,getuid(),getgid()),"writer-boundary-first");
        r=receive(fd[0],32,128,0); check(r.n==4 && identity(r,q,getuid(),getgid()),"writer-boundary-second");
    }
    size_t caps[] = {0,8,16,17,20,27,28,31,32};
    for (size_t i=0;i<sizeof(caps)/sizeof(caps[0]);++i) {
        must(send(fd[1],"ping",4,0)==4,"send"); r=receive(fd[0],32,caps[i],0);
        size_t n = caps[i] < CMSG_LEN(0) ? 0 : caps[i] - CMSG_LEN(0);
        if(n>sizeof(self)) n=sizeof(self);
        size_t used = caps[i]<CMSG_LEN(0) ? 0 : caps[i];
        if(used>CMSG_SPACE(sizeof(self))) used=CMSG_SPACE(sizeof(self));
        char name[64]; snprintf(name,sizeof(name),"control-capacity-%zu",caps[i]);
        check(r.n==4 && r.control==used && !!(r.flags & MSG_CTRUNC)==(caps[i]<CMSG_LEN(sizeof(self))) &&
              r.found==(caps[i]>=CMSG_LEN(0)) && r.rawlen==n && !memcmp(r.raw,&self,n),name);
    }
    option(fd[0],0); must(send(fd[1],"ping",4,0)==4,"send");
    r=receive(fd[0],32,128,0);check(r.n==4 && !r.found && !r.control,"disabled");
    must(send(fd[1],"ping",4,0)==4,"send"); option(fd[0],1);
    r=receive(fd[0],32,128,0);check(r.n==4 && identity(r,0,65534,65534),"enabled-after-unrecorded-send");
    must(send(fd[1],"ping",4,0)==4,"send");option(fd[0],0);
    r=receive(fd[0],32,128,0);check(r.n==4 && !r.found,"disabled-before-receive");
    option(fd[1],1);must(send(fd[1],"ping",4,0)==4,"send");option(fd[0],1);
    r=receive(fd[0],32,128,0);check(r.n==4 && identity(r,getpid(),getuid(),getgid()),"sender-enabled");
    check(explicit_send(fd[1],self,sizeof(self),-1)==4,"explicit-self-send");
    r=receive(fd[0],32,128,0);check(r.n==4 && identity(r,getpid(),getuid(),getgid()),"explicit-self-receive");
    struct ucred bad=self; bad.pid=getppid(); errno=0;
    check(explicit_send(fd[1],bad,sizeof(bad),-1)==-1 && errno==EPERM,"forged-pid-rejected");
    bad=self;bad.uid=0;errno=0;check(explicit_send(fd[1],bad,sizeof(bad),-1)==-1 && errno==EPERM,"forged-uid-rejected");
    bad=self;bad.gid=0;errno=0;check(explicit_send(fd[1],bad,sizeof(bad),-1)==-1 && errno==EPERM,"forged-gid-rejected");
    errno=0;check(explicit_send(fd[1],self,sizeof(self)-1,-1)==-1 && errno==EINVAL,"short-credentials-rejected");
    errno=0;check(explicit_send(fd[1],self,sizeof(self)+1,-1)==-1 && errno==EINVAL,"long-credentials-rejected");
    int right=open("/dev/null",O_RDONLY);must(right>=0,"open");
    check(explicit_send(fd[1],self,sizeof(self),right)==4,"send-rights");
    for(int i=0;i<3;++i) {
        r=receive(fd[0],32,128,(i<2?MSG_PEEK:0)|MSG_CMSG_CLOEXEC);
        int ok=r.n==4 && identity(r,getpid(),getuid(),getgid()) && r.rights==1 && r.fd>=0 &&
            (fcntl(r.fd,F_GETFD)&FD_CLOEXEC);
        check(ok,i<2?"rights-credentials-peek":"rights-credentials-consume");
        if(r.fd>=0) close(r.fd);
    }
    close(right);
    uid_t uid[3];gid_t gid[3];
    must(getresuid(&uid[0],&uid[1],&uid[2])==0,"getresuid");
    must(getresgid(&gid[0],&gid[1],&gid[2])==0,"getresgid");
    for(int i=0;i<3;++i) {
        struct ucred allowed={getpid(),uid[i],gid[i]};
        check(explicit_send(fd[1],allowed,sizeof(allowed),-1)==4,"explicit-real-effective-saved-send");
        r=receive(fd[0],32,128,0);
        check(r.n==4 && identity(r,getpid(),uid[i],gid[i]),"explicit-real-effective-saved-receive");
    }
    close(fd[0]);close(fd[1]);
}
int main(int argc, char **argv) {
    setvbuf(stdout,NULL,_IONBF,0);
    if (argc==2 && !strcmp(argv[1],"mixed-ids")) {
        must(setresgid(1000,1001,1002)==0,"setresgid");
        must(setresuid(1000,1001,1002)==0,"setresuid");
    }
    must(getuid()!=0 && getgid()!=0,"run as unprivileged user");
    run_type(SOCK_SEQPACKET);run_type(SOCK_DGRAM);run_type(SOCK_STREAM);
    printf("CREDENTIALS_SUMMARY checks=%d failures=%d\n",checks,failures);
    return failures?1:0;
}
