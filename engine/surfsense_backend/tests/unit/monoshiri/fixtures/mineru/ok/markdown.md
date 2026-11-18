Therefore,

$$
\frac {\partial \phi (x , y)}{\partial x} = \frac {\partial}{\partial x} \Phi (z, \bar {z}) = \frac {\partial \Phi}{\partial z} \frac {\partial z}{\partial x} + \frac {\partial \Phi}{\partial \bar {z}} \frac {\partial \bar {z}}{\partial x} = \frac {\partial \Phi}{\partial z} + \frac {\partial \Phi}{\partial \bar {z}} = \left(\frac {\partial}{\partial z} + \frac {\partial}{\partial \bar {z}}\right) \Phi
$$

$$
\frac {\partial \phi (x , y)}{\partial y} = \frac {\partial}{\partial y} \Phi (z, \bar {z}) = \frac {\partial \Phi}{\partial z} \frac {\partial z}{\partial y} + \frac {\partial \Phi}{\partial \bar {z}} \frac {\partial \bar {z}}{\partial y} = i \left(\frac {\partial \Phi}{\partial z} - \frac {\partial \Phi}{\partial \bar {z}}\right) = i \left(\frac {\partial}{\partial z} - \frac {\partial}{\partial \bar {z}}\right) \Phi
$$

$$
\frac {\partial^ {2} \phi (x , y)}{\partial x ^ {2}} = \left(\frac {\partial}{\partial z} + \frac {\partial}{\partial \overline {{z}}}\right) \left(\frac {\partial}{\partial z} + \frac {\partial}{\partial \overline {{z}}}\right) \Phi = \left(\frac {\partial^ {2} \Phi}{\partial z ^ {2}} + 2 \frac {\partial^ {2} \Phi}{\partial z \partial \overline {{z}}} + \frac {\partial^ {2} \Phi}{\partial \overline {{z}} ^ {2}}\right)
$$

$$
\frac {\partial^ {2} \phi (x , y)}{\partial y ^ {2}} = - \left(\frac {\partial}{\partial z} - \frac {\partial}{\partial \bar {z}}\right) \left(\frac {\partial}{\partial z} - \frac {\partial}{\partial \bar {z}}\right) \Phi = \left(- \frac {\partial^ {2} \Phi}{\partial z ^ {2}} + 2 \frac {\partial^ {2} \Phi}{\partial z \partial \bar {z}} - \frac {\partial^ {2} \Phi}{\partial \bar {z} ^ {2}}\right)\tag{1.156}
$$

$$
\left| \frac {\partial^ {2} \phi (x , y)}{\partial x \partial y} = i \left(\frac {\partial}{\partial z} + \frac {\partial}{\partial \bar {z}}\right) \left(\frac {\partial}{\partial z} - \frac {\partial}{\partial \bar {z}}\right) \Phi = i \left(\frac {\partial^ {2}}{\partial z ^ {2}} - \frac {\partial^ {2}}{\partial \bar {z} ^ {2}}\right) \Phi \right|
$$

$$
\therefore \nabla^ {2} \phi (x, y) = \frac {\partial^ {2} \phi (x , y)}{\partial x ^ {2}} + \frac {\partial^ {2} \phi (x , y)}{\partial y ^ {2}} = 4 \frac {\partial^ {2} \Phi (z , \bar {z})}\partial z \partial \bar {z}
$$

$$
\therefore \nabla^ {4} \phi (x, y) = 1 6 \frac {\partial^ {4} \Phi (z , \overline {{z}})}{\partial z ^ {2} \partial \overline {{z}} ^ {2}}
$$

From equation (1.102) it is known that the compatibility condition implies that the Airy stress function must be biharmonic. Therefore, the compatibility condition takes the following form:

$$
\nabla^ {4} \phi (x, y) = 1 6 \frac {\partial^ {4} \Phi (z , \overline {{z}})}{\partial z ^ {2} \partial \overline {{z}} ^ {2}} = 0\tag{1.157}
$$

## 1.3.4.3.2 Stress Fields in Terms of Complex Potential Functions

Note that if $\chi _ { 1 } , \chi _ { 2 } , \chi _ { 3 } ,$ and $\chi _ { 4 }$ are four analytic (or differentiable) functions of $z$ or $\overline { { z } } _ { i }$ then the following $\Phi ( z , { \overline { { z } } } )$ should satisfy equation (1.157):

$$
\Phi (z, \bar {z}) = z \chi_ {1} (\bar {z}) + \bar {z} \chi_ {2} (z) + \chi_ {3} (z) + \chi_ {4} (\bar {z}) = z \bar {\chi} _ {1} (z) + \bar {z} \chi_ {2} (z) + \chi_ {3} (z) + \bar {\chi} _ {4} (z)\tag{1.158}
$$

Since Airy stress function must be a real function, one can write

$$
\chi_ {1} (z) = \chi_ {2} (z) = \frac {1}{2} \phi (z)\tag{1.159}
$$

$$
\chi_ {3} (z) = \chi_ {4} (z) = \frac {1}{2} \psi (z)
$$

Substituting equation (1.159) into equation (1.158), one gets

$$
\Phi (z, \bar {z}) = z \chi_ {1} (\bar {z}) + \bar {z} \chi_ {2} (z) + \chi_ {3} (z) + \chi_ {4} (\bar {z}) = \frac {1}{2} [ z \phi (\bar {z}) + \bar {z} \phi (z) + \psi (z) + \psi (\bar {z}) ]\tag{1.160}
$$

Therefore,

$$
\frac {\partial \Phi (z , \bar {z})}{\partial z} = \frac {1}{2} \left[ \phi (\bar {z}) + \bar {z} \frac {\partial \phi (z)}{\partial z} + \frac {\partial \psi (z)}{\partial z} \right] = \frac {1}{2} \left[ \phi (\bar {z}) + \bar {z} \phi^ {\prime} (z) + \psi^ {\prime} (z) \right]
$$

$$
\frac {\partial^ {2} \Phi (z , \overline {{z}})}{\partial z ^ {2}} = \frac {1}{2} [ \overline {{z}} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ]\tag{1.161}
$$

$$
\frac {\partial^ {2} \Phi (z , \bar {z})}{\partial z \partial \bar {z}} = \frac {1}{2} \left[ \phi^ {\prime} (\bar {z}) + \phi^ {\prime} (z) \right]
$$

From equations (1.101), (1.156), and (1.161), one obtains

$$
\sigma_ {x x} + \sigma_ {y y} = \frac {\partial^ {2} \phi}{\partial y ^ {2}} + \frac {\partial^ {2} \phi}{\partial x ^ {2}} = 4 \frac {\partial^ {2} \Phi}{\partial z \partial \bar {z}} = 2 [ \phi^ {\prime} (z) + \phi^ {\prime} (\bar {z}) ] = 2 [ \phi^ {\prime} (z) + \bar {\phi} ^ {\prime} (z) ]\tag{1.162}
$$

and

$$
\begin{array}{l} \sigma_ {y y} - \sigma_ {x x} + 2 i \sigma_ {x y} = \frac {\partial^ {2} \phi}{\partial x ^ {2}} - \frac {\partial^ {2} \phi}{\partial y ^ {2}} - 2 i \frac {\partial^ {2} \phi}{\partial x \partial y} \\ = \left(\frac {\partial^ {2} \Phi}{\partial z ^ {2}} + 2 \frac {\partial^ {2} \Phi}{\partial z \partial \overline {{z}}} + \frac {\partial^ {2} \Phi}{\partial \overline {{z}} ^ {2}}\right) - \left(- \frac {\partial^ {2} \Phi}{\partial z ^ {2}} + 2 \frac {\partial^ {2} \Phi}{\partial z \partial \overline {{z}}} - \frac {\partial^ {2} \Phi}{\partial \overline {{z}} ^ {2}}\right) + 2 \left(\frac {\partial^ {2} \Phi}{\partial z ^ {2}} - \frac {\partial^ {2} \Phi}{\partial \overline {{z}} ^ {2}}\right) \\ = 4 \frac {\partial^ {2} \Phi}{\partial z ^ {2}} = 2 [ \overline {{z}} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ] \end{array}\tag{1.163}
$$

From the stress transformation law it is possible to show that the stress fields in the $( r ,   \theta )$ polar coordinate system are given by

$$
\sigma_ {r r} + \sigma_ {\theta \theta} = \sigma_ {x x} + \sigma_ {y y} = 2 [ \phi^ {\prime} (z) + \phi^ {\prime} (\bar {z}) ] = 2 [ \phi^ {\prime} (z) + \bar {\phi} ^ {\prime} (z) ]\tag{1.164}
$$

$$
\sigma_ {\theta \theta} - \sigma_ {r r} + 2 i \sigma_ {r \theta} = [ \sigma_ {y y} - \sigma_ {x x} + 2 i \sigma_ {x y} ] e ^ {2 i \theta} = 2 [ \bar {z} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ] e ^ {2 i \theta}\tag{1.165}
$$

Subtracting equation (1.165) from equation (1.164), one obtains

$$
\sigma_ {r r} - i \sigma_ {\tau \theta} = \phi^ {\prime} (z) + \bar {\phi} ^ {\prime} (z) - [ \bar {z} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ] e ^ {2 i \theta}\tag{1.166}
$$

Let us specialize the expressions given in equations (1.164) to (1.166) for the following two special cases: (a) $\phi ^ { \prime } ( z )   =   A _ { n } z ^ { n } ,   \psi ^ { \prime \prime } ( z )   =   0 ,$ and (b) $\psi ^ { \prime \prime } ( z )   =   B _ { m } z ^ { m } ,$ $\phi ^ { \prime } ( z ) = 0$

**Stress fields for** $\phi ^ { \prime } ( z ) = A _ { n } z ^ { n } , \; \psi ^ { \prime \prime } ( z ) = 0 { : }$

$$
\phi^ {\prime} (z) = A _ {n} z ^ {n} = A _ {n} r ^ {n} e ^ {i n \theta}
$$

Therefore, if $A _ { n }$ is a real number,

$$
\begin{array}{c} \sigma_ {r r} + \sigma_ {\theta \theta} = 2 [ \phi^ {\prime} (z) + \overline {{\phi}} ^ {\prime} (z) ] = 2 A _ {n} r ^ {n} [ e ^ {i n \theta} + e ^ {- i n \theta} ] = 4 A _ {n} r ^ {n} \cos n \theta \\ \sigma_ {\theta \theta} - \sigma_ {r r} + 2 i \sigma_ {r \theta} = 2 [ \overline {{z}} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ] e ^ {2 i \theta} \\ \qquad = 2 [ r e ^ {- i \theta} n A _ {n} r ^ {n - 1} e ^ {i (n - 1) \theta} ] e ^ {2 i \theta} = 2 n A _ {n} r ^ {n} e ^ {i n \theta} \\ \sigma_ {r r} - i \sigma_ {r \theta} = \phi^ {\prime} (z) + \overline {{\phi}} ^ {\prime} (z) - [ \overline {{z}} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ] e ^ {2 i \theta} \\ \qquad = A _ {n} r ^ {n} [ e ^ {i n \theta} + e ^ {- i n \theta} ] - n A _ {n} r ^ {n} e ^ {i n \theta} = A _ {n} r ^ {n} [ (1 - n) e ^ {i n \theta} + e ^ {- i n \theta} ] \end{array}\tag{1.167}
$$

If $A _ { n }$ is an imaginary number,

$$
\begin{array}{c} \sigma_ {r r} + \sigma_ {\theta \theta} = 2 A _ {n} r ^ {n} [ e ^ {i n \theta} - e ^ {- i n \theta} ] = 4 i A _ {n} r ^ {n} \sin n \theta \\ \sigma_ {\theta \theta} - \sigma_ {r r} + 2 i \sigma_ {r \theta} = 2 [ r e ^ {- i \theta} n A _ {n} r ^ {n - 1} e ^ {i (n - 1) \theta} ] e ^ {2 i \theta} = 2 n A _ {n} r ^ {n} e ^ {i n \theta} \\ \sigma_ {r r} - i \sigma_ {r \theta} = \phi^ {\prime} (z) + \bar {\phi} ^ {\prime} (z) - [ \overline {{z}} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ] e ^ {2 i \theta} \\ = A _ {n} r ^ {n} [ e ^ {i n \theta} - e ^ {- i n \theta} ] - n A _ {n} r ^ {n} e ^ {i n \theta} = A _ {n} r ^ {n} [ (1 - n) e ^ {i n \theta} - e ^ {- i n \theta} ] \end{array}\tag{1.168}
$$

Equations (1.167) and (1.168) can be combined to obtain

$$
\left| \begin{array}{c}\sigma_ {r r} + \sigma_ {\theta \theta} = 2 A _ {n} r ^ {n} [ e ^ {i n \theta} \pm e ^ {- i n \theta} ] \\ \sigma_ {\theta \theta} - \sigma_ {r r} + 2 i \sigma_ {r \theta} = 2 n A _ {n} r ^ {n} e ^ {i n \theta} \\ \sigma_ {r r} - i \sigma_ {r \theta} = A _ {n} r ^ {n} [ (1 - n) e ^ {i n \theta} \pm e ^ {- i n \theta} ] \end{array} \right|\tag{1.169}
$$

In this equation, the plus (+) sign is for the case when $A _ { n }$ is real and the minus (–) sign is for imaginary $A _ { n } .$

**Stress fields for** $\psi ^ { \prime \prime } ( z ) = B _ { m } z ^ { m } , \; \theta ^ { \prime } ( z ) = 0$

For the case $\psi ^ { \prime \prime } ( z )   =   B _ { m } z ^ { m }   =   B _ { m } r ^ { m } e ^ { i m \theta } \mathrm { a n d } \phi ^ { \prime } ( z )   =   0 ,$

$$
\begin{array}{c} \sigma_ {r r} + \sigma_ {\theta \theta} = 2 [ \phi^ {\prime} (z) + \bar {\phi} ^ {\prime} (z) ] = 0 \\ \sigma_ {\theta \theta} - \sigma_ {r r} + 2 i \sigma_ {r \theta} = 2 [ \overline {{z}} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ] e ^ {2 i \theta} = 2 [ B _ {m} r ^ {m} e ^ {i m \theta} ] e ^ {2 i \theta} = 2 B _ {m} r ^ {m} e ^ {i (m + 2) \theta} \\ \sigma_ {r r} - i \sigma_ {r \theta} = \phi^ {\prime} (z) + \bar {\phi} ^ {\prime} (z) - [ \overline {{z}} \phi^ {\prime \prime} (z) + \psi^ {\prime \prime} (z) ] e ^ {2 i \theta} = - B _ {m} r ^ {m} e ^ {i (m + 2) \theta} \end{array}\tag{1.170}
$$

Note that equation (1.170) is valid for both real and imaginary values of $B _ { m } .$

**Example 1.12**

Obtain the complex potential functions $\theta ^ { \prime } ( z )$ and $\psi ^ { \prime \prime } ( z ) ,$ , given in equations (1.162) and (1.163), for a plate under uniaxial tension $\sigma _ { y y } = \sigma _ { 0 } , \; \sigma _ { x x } =$ $\sigma _ { x y } = 0$