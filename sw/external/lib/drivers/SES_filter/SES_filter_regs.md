<table class="regdef" id="Reg_ses_control">
 <tr>
  <th class="regdef" colspan=5>
   <div>SES_filter.ses_control @ 0x0</div>
   <div><p>control register of the SES filter</p></div>
   <div>Reset default = 0x0, mask 0x1</div>
  </th>
 </tr>
<tr><td colspan=5><table class="regpic"><tr><td class="bitnum">31</td><td class="bitnum">30</td><td class="bitnum">29</td><td class="bitnum">28</td><td class="bitnum">27</td><td class="bitnum">26</td><td class="bitnum">25</td><td class="bitnum">24</td><td class="bitnum">23</td><td class="bitnum">22</td><td class="bitnum">21</td><td class="bitnum">20</td><td class="bitnum">19</td><td class="bitnum">18</td><td class="bitnum">17</td><td class="bitnum">16</td></tr><tr><td class="unused" colspan=16>&nbsp;</td>
</tr>
<tr><td class="bitnum">15</td><td class="bitnum">14</td><td class="bitnum">13</td><td class="bitnum">12</td><td class="bitnum">11</td><td class="bitnum">10</td><td class="bitnum">9</td><td class="bitnum">8</td><td class="bitnum">7</td><td class="bitnum">6</td><td class="bitnum">5</td><td class="bitnum">4</td><td class="bitnum">3</td><td class="bitnum">2</td><td class="bitnum">1</td><td class="bitnum">0</td></tr><tr><td class="unused" colspan=15>&nbsp;</td>
<td class="fname" colspan=1 style="font-size:27.272727272727273%">ses_control</td>
</tr></table></td></tr>
<tr><th width=5%>Bits</th><th width=5%>Type</th><th width=5%>Reset</th><th>Name</th><th>Description</th></tr><tr><td class="regbits">0</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">ses_control</td><td class="regde"></td></table>
<br>
<table class="regdef" id="Reg_ses_status">
 <tr>
  <th class="regdef" colspan=5>
   <div>SES_filter.ses_status @ 0x4</div>
   <div><p>status register of the SES filter</p></div>
   <div>Reset default = 0x0, mask 0x3</div>
  </th>
 </tr>
<tr><td colspan=5><table class="regpic"><tr><td class="bitnum">31</td><td class="bitnum">30</td><td class="bitnum">29</td><td class="bitnum">28</td><td class="bitnum">27</td><td class="bitnum">26</td><td class="bitnum">25</td><td class="bitnum">24</td><td class="bitnum">23</td><td class="bitnum">22</td><td class="bitnum">21</td><td class="bitnum">20</td><td class="bitnum">19</td><td class="bitnum">18</td><td class="bitnum">17</td><td class="bitnum">16</td></tr><tr><td class="unused" colspan=16>&nbsp;</td>
</tr>
<tr><td class="bitnum">15</td><td class="bitnum">14</td><td class="bitnum">13</td><td class="bitnum">12</td><td class="bitnum">11</td><td class="bitnum">10</td><td class="bitnum">9</td><td class="bitnum">8</td><td class="bitnum">7</td><td class="bitnum">6</td><td class="bitnum">5</td><td class="bitnum">4</td><td class="bitnum">3</td><td class="bitnum">2</td><td class="bitnum">1</td><td class="bitnum">0</td></tr><tr><td class="unused" colspan=14>&nbsp;</td>
<td class="fname" colspan=2 style="font-size:60.0%">ses_status</td>
</tr></table></td></tr>
<tr><th width=5%>Bits</th><th width=5%>Type</th><th width=5%>Reset</th><th>Name</th><th>Description</th></tr><tr><td class="regbits">1:0</td><td class="regperm">ro</td><td class="regrv">x</td><td class="regfn">ses_status</td><td class="regde"></td></table>
<br>
<table class="regdef" id="Reg_ses_window_size">
 <tr>
  <th class="regdef" colspan=5>
   <div>SES_filter.ses_window_size @ 0x8</div>
   <div><p>Size of the window (Ww) of the SES filter</p></div>
   <div>Reset default = 0x0, mask 0x1f</div>
  </th>
 </tr>
<tr><td colspan=5><table class="regpic"><tr><td class="bitnum">31</td><td class="bitnum">30</td><td class="bitnum">29</td><td class="bitnum">28</td><td class="bitnum">27</td><td class="bitnum">26</td><td class="bitnum">25</td><td class="bitnum">24</td><td class="bitnum">23</td><td class="bitnum">22</td><td class="bitnum">21</td><td class="bitnum">20</td><td class="bitnum">19</td><td class="bitnum">18</td><td class="bitnum">17</td><td class="bitnum">16</td></tr><tr><td class="unused" colspan=16>&nbsp;</td>
</tr>
<tr><td class="bitnum">15</td><td class="bitnum">14</td><td class="bitnum">13</td><td class="bitnum">12</td><td class="bitnum">11</td><td class="bitnum">10</td><td class="bitnum">9</td><td class="bitnum">8</td><td class="bitnum">7</td><td class="bitnum">6</td><td class="bitnum">5</td><td class="bitnum">4</td><td class="bitnum">3</td><td class="bitnum">2</td><td class="bitnum">1</td><td class="bitnum">0</td></tr><tr><td class="unused" colspan=11>&nbsp;</td>
<td class="fname" colspan=5>ses_window_size</td>
</tr></table></td></tr>
<tr><th width=5%>Bits</th><th width=5%>Type</th><th width=5%>Reset</th><th>Name</th><th>Description</th></tr><tr><td class="regbits">4:0</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">ses_window_size</td><td class="regde"></td></table>
<br>
<table class="regdef" id="Reg_ses_decim_factor">
 <tr>
  <th class="regdef" colspan=5>
   <div>SES_filter.ses_decim_factor @ 0xc</div>
   <div><p>Decimation factor for the downsampling of the SES filter (decim_factor)</p></div>
   <div>Reset default = 0x0, mask 0x3ff</div>
  </th>
 </tr>
<tr><td colspan=5><table class="regpic"><tr><td class="bitnum">31</td><td class="bitnum">30</td><td class="bitnum">29</td><td class="bitnum">28</td><td class="bitnum">27</td><td class="bitnum">26</td><td class="bitnum">25</td><td class="bitnum">24</td><td class="bitnum">23</td><td class="bitnum">22</td><td class="bitnum">21</td><td class="bitnum">20</td><td class="bitnum">19</td><td class="bitnum">18</td><td class="bitnum">17</td><td class="bitnum">16</td></tr><tr><td class="unused" colspan=16>&nbsp;</td>
</tr>
<tr><td class="bitnum">15</td><td class="bitnum">14</td><td class="bitnum">13</td><td class="bitnum">12</td><td class="bitnum">11</td><td class="bitnum">10</td><td class="bitnum">9</td><td class="bitnum">8</td><td class="bitnum">7</td><td class="bitnum">6</td><td class="bitnum">5</td><td class="bitnum">4</td><td class="bitnum">3</td><td class="bitnum">2</td><td class="bitnum">1</td><td class="bitnum">0</td></tr><tr><td class="unused" colspan=6>&nbsp;</td>
<td class="fname" colspan=10>ses_decim_factor</td>
</tr></table></td></tr>
<tr><th width=5%>Bits</th><th width=5%>Type</th><th width=5%>Reset</th><th>Name</th><th>Description</th></tr><tr><td class="regbits">9:0</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">ses_decim_factor</td><td class="regde"></td></table>
<br>
<table class="regdef" id="Reg_ses_sysclk_division">
 <tr>
  <th class="regdef" colspan=5>
   <div>SES_filter.ses_sysclk_division @ 0x10</div>
   <div><p>Decimation factor from the system clock to the SES filter clock</p></div>
   <div>Reset default = 0x0, mask 0x3ff</div>
  </th>
 </tr>
<tr><td colspan=5><table class="regpic"><tr><td class="bitnum">31</td><td class="bitnum">30</td><td class="bitnum">29</td><td class="bitnum">28</td><td class="bitnum">27</td><td class="bitnum">26</td><td class="bitnum">25</td><td class="bitnum">24</td><td class="bitnum">23</td><td class="bitnum">22</td><td class="bitnum">21</td><td class="bitnum">20</td><td class="bitnum">19</td><td class="bitnum">18</td><td class="bitnum">17</td><td class="bitnum">16</td></tr><tr><td class="unused" colspan=16>&nbsp;</td>
</tr>
<tr><td class="bitnum">15</td><td class="bitnum">14</td><td class="bitnum">13</td><td class="bitnum">12</td><td class="bitnum">11</td><td class="bitnum">10</td><td class="bitnum">9</td><td class="bitnum">8</td><td class="bitnum">7</td><td class="bitnum">6</td><td class="bitnum">5</td><td class="bitnum">4</td><td class="bitnum">3</td><td class="bitnum">2</td><td class="bitnum">1</td><td class="bitnum">0</td></tr><tr><td class="unused" colspan=6>&nbsp;</td>
<td class="fname" colspan=10>ses_sysclk_division</td>
</tr></table></td></tr>
<tr><th width=5%>Bits</th><th width=5%>Type</th><th width=5%>Reset</th><th>Name</th><th>Description</th></tr><tr><td class="regbits">9:0</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">ses_sysclk_division</td><td class="regde"></td></table>
<br>
<table class="regdef" id="Reg_ses_activated_stages">
 <tr>
  <th class="regdef" colspan=5>
   <div>SES_filter.ses_activated_stages @ 0x14</div>
   <div><p>Thermometric value of the activated stages (The 1s should be contiguous and right-aligned)</p></div>
   <div>Reset default = 0x0, mask 0x3f</div>
  </th>
 </tr>
<tr><td colspan=5><table class="regpic"><tr><td class="bitnum">31</td><td class="bitnum">30</td><td class="bitnum">29</td><td class="bitnum">28</td><td class="bitnum">27</td><td class="bitnum">26</td><td class="bitnum">25</td><td class="bitnum">24</td><td class="bitnum">23</td><td class="bitnum">22</td><td class="bitnum">21</td><td class="bitnum">20</td><td class="bitnum">19</td><td class="bitnum">18</td><td class="bitnum">17</td><td class="bitnum">16</td></tr><tr><td class="unused" colspan=16>&nbsp;</td>
</tr>
<tr><td class="bitnum">15</td><td class="bitnum">14</td><td class="bitnum">13</td><td class="bitnum">12</td><td class="bitnum">11</td><td class="bitnum">10</td><td class="bitnum">9</td><td class="bitnum">8</td><td class="bitnum">7</td><td class="bitnum">6</td><td class="bitnum">5</td><td class="bitnum">4</td><td class="bitnum">3</td><td class="bitnum">2</td><td class="bitnum">1</td><td class="bitnum">0</td></tr><tr><td class="unused" colspan=10>&nbsp;</td>
<td class="fname" colspan=6 style="font-size:90.0%">ses_activated_stages</td>
</tr></table></td></tr>
<tr><th width=5%>Bits</th><th width=5%>Type</th><th width=5%>Reset</th><th>Name</th><th>Description</th></tr><tr><td class="regbits">5:0</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">ses_activated_stages</td><td class="regde"></td></table>
<br>
<table class="regdef" id="Reg_ses_gain_stage">
 <tr>
  <th class="regdef" colspan=5>
   <div>SES_filter.ses_gain_stage @ 0x18</div>
   <div><p>Input gain of the different stages (WgX)</p></div>
   <div>Reset default = 0x0, mask 0x3fffffff</div>
  </th>
 </tr>
<tr><td colspan=5><table class="regpic"><tr><td class="bitnum">31</td><td class="bitnum">30</td><td class="bitnum">29</td><td class="bitnum">28</td><td class="bitnum">27</td><td class="bitnum">26</td><td class="bitnum">25</td><td class="bitnum">24</td><td class="bitnum">23</td><td class="bitnum">22</td><td class="bitnum">21</td><td class="bitnum">20</td><td class="bitnum">19</td><td class="bitnum">18</td><td class="bitnum">17</td><td class="bitnum">16</td></tr><tr><td class="unused" colspan=2>&nbsp;</td>
<td class="fname" colspan=5>gain_stg_5</td>
<td class="fname" colspan=5>gain_stg_4</td>
<td class="fname" colspan=4>gain_stg_3...</td>
</tr>
<tr><td class="bitnum">15</td><td class="bitnum">14</td><td class="bitnum">13</td><td class="bitnum">12</td><td class="bitnum">11</td><td class="bitnum">10</td><td class="bitnum">9</td><td class="bitnum">8</td><td class="bitnum">7</td><td class="bitnum">6</td><td class="bitnum">5</td><td class="bitnum">4</td><td class="bitnum">3</td><td class="bitnum">2</td><td class="bitnum">1</td><td class="bitnum">0</td></tr><tr><td class="fname" colspan=1 style="font-size:23.076923076923077%">...gain_stg_3</td>
<td class="fname" colspan=5>gain_stg_2</td>
<td class="fname" colspan=5>gain_stg_1</td>
<td class="fname" colspan=5>gain_stg_0</td>
</tr></table></td></tr>
<tr><th width=5%>Bits</th><th width=5%>Type</th><th width=5%>Reset</th><th>Name</th><th>Description</th></tr><tr><td class="regbits">4:0</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">gain_stg_0</td><td class="regde"><p>Value of the input gain for the stage no 0</p></td><tr><td class="regbits">9:5</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">gain_stg_1</td><td class="regde"><p>Value of the input gain for the stage no 1</p></td><tr><td class="regbits">14:10</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">gain_stg_2</td><td class="regde"><p>Value of the input gain for the stage no 2</p></td><tr><td class="regbits">19:15</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">gain_stg_3</td><td class="regde"><p>Value of the input gain for the stage no 3</p></td><tr><td class="regbits">24:20</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">gain_stg_4</td><td class="regde"><p>Value of the input gain for the stage no 4</p></td><tr><td class="regbits">29:25</td><td class="regperm">rw</td><td class="regrv">x</td><td class="regfn">gain_stg_5</td><td class="regde"><p>Value of the input gain for the stage no 5</p></td></table>
<br>
<table class="regdef" id="Reg_rx_data">
  <tr>
    <th class="regdef">
      <div>SES_filter.rx_data @ + 0x1c</div>
      <div>1 item ro window</div>
      <div>Byte writes are <i>not</i> supported</div>
    </th>
  </tr>
<tr><td><table class="regpic"><tr><td width="10%"></td><td class="bitnum">31</td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum"></td><td class="bitnum">0</td></tr><tr><td class="regbits">+0x1c</td><td class="fname" colspan=32>&nbsp;</td>
</tr><tr><td class="regbits">+0x20</td><td class="fname" colspan=32>&nbsp;</td>
</tr><tr><td>&nbsp;</td><td align=center colspan=32>...</td></tr><tr><td class="regbits">+0x18</td><td class="fname" colspan=32>&nbsp;</td>
</tr><tr><td class="regbits">+0x1c</td><td class="fname" colspan=32>&nbsp;</td>
</tr></td></tr></table><tr><td class="regde"><p>Filtered output</p></td></tr></table>
<br>
