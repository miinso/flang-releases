module e2e_m
  use iso_c_binding
  implicit none
  type :: shape_t
    integer :: id = 7
    real, allocatable :: w(:)
  contains
    procedure :: area
    final :: done
  end type
  integer :: finalized = 0
  interface
    function c_strlen(s) bind(c, name="strlen") result(n)
      import
      character(kind=c_char), intent(in) :: s(*)
      integer(c_size_t) :: n
    end function
  end interface
contains
  real function area(self)
    class(shape_t), intent(in) :: self
    area = sum(self%w) * self%id
  end function
  subroutine done(self)
    type(shape_t), intent(inout) :: self
    finalized = finalized + 1
  end subroutine
  subroutine bump()
    integer :: n
    common /blk/ n
    n = n + 4
  end subroutine
  subroutine scope()
    type(shape_t) :: s
    s%w = [1.0, 2.0]
    print '(a,f5.1)', "bound ", s%area()
  end subroutine
end module

program e2e
  use e2e_m
  implicit none
  integer, allocatable :: a(:,:)
  integer, pointer :: p(:,:)
  integer, target :: t(3,4)
  class(shape_t), allocatable :: poly(:)
  type(shape_t) :: one
  character(len=:), allocatable :: s
  character(len=40) :: line
  integer :: n, ni
  logical :: ex
  common /blk/ n
  namelist /small/ ni

  allocate(a(3,4)); a = 2
  print '(a,3i4)', "alloc ", shape(a), sum(a)
  t = 1
  p(0:2, 0:3) => t
  print '(a,4i3)', "bounds", lbound(p), ubound(p)
  call sub(a(2:3, ::2))
  one%w = [0.5]
  allocate(poly(3), source=one)
  print '(a,3i2,f5.1)', "source", poly%id, poly(3)%area()
  call scope()
  print '(a,i2)', "final ", finalized
  s = "wasm" // repeat("!", 3)
  print '(a,a,i3)', "defer ", s, len(s)
  print '(a,i3,i2)', "cstr  ", int(c_strlen("hello" // c_null_char)), int(c_size_t)
  n = 3; call bump()
  print '(a,i2)', "common", n
  ni = 5
  write(line, nml=small)
  print '(a,a)', "nml   ", trim(adjustl(line))
  inquire(file="no_such_file_here", exist=ex)
  print '(a,l2)', "inq   ", ex
contains
  subroutine sub(x)
    integer :: x(:,:)
    print '(a,3i3)', "sect  ", size(x), shape(x)
  end subroutine
end program
