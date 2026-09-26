#define _GNU_SOURCE
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
static void check(int ok, const char *name) { if (!ok) {perror(name); exit(1);} printf("PASS %s\n",name); }
int main(void) {
 size_t p=(size_t)sysconf(_SC_PAGESIZE); unsigned char *a=mmap(NULL,5*p,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
 check(a!=MAP_FAILED,"anonymous allocation"); memset(a,0x5a,5*p);
 check(madvise(a+p,3*p,MADV_DONTNEED)==0,"middle discard");
 for(size_t n=p;n<4*p;n++) if(a[n]!=0) return 2;
 check(a[0]==0x5a && a[p-1]==0x5a && a[4*p]==0x5a && a[5*p-1]==0x5a,"zero refault and neighbours");
 memset(a+p,0xa5,3*p); pid_t child=fork(); check(child>=0,"fork");
 if(!child) {if(madvise(a+p,3*p,MADV_DONTNEED))_exit(3); for(size_t n=p;n<4*p;n++)if(a[n])_exit(4); if(a[0]!=0x5a||a[4*p]!=0x5a)_exit(5); a[p]=0x33; _exit(0);}
 int s;check(waitpid(child,&s,0)==child && WIFEXITED(s) && !WEXITSTATUS(s),"child discard and zero refault");
 for(size_t n=p;n<4*p;n++)if(a[n]!=0xa5)return 6;
 check(a[0]==0x5a&&a[4*p]==0x5a,"parent COW data survives child discard");check(!munmap(a,5*p),"cleanup");puts("MADVISE_NEIGHBOURS_OK");return 0;
}
